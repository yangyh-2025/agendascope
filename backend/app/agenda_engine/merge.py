"""次日自动归并（T3.3，详细设计 4.2 算法 3 + ADR-006 自我纠错）。

候选集 C：merged_into IS NULL AND lifecycle_state IN ('nascent','forming')
          （昨日至今的新议题/微簇，详细设计 4.2 算法 3 口径）
          AND first_seen_at >= candidate_since（默认近 24h，估算）
          按 first_seen_at 升序，老议题优先被并入目标
档案集 D：merged_into IS NULL AND lifecycle_state != 'archived'
          AND last_seen_at >= now() - merge_active_days

no_merge_pairs：∪ topics.no_merge_with（人工误并回滚名单先行排除）
human_locked_fields 含 'merged_into' 的源议题不自动归并（人工优先）

归并动作（单议题事务内，多议题由调用方提交）：
  - c.merged_into = target.id, c.lifecycle_state='evolving'
  - topic_articles 迁移：c → target（assign_method='merge'，保留 weight）
  - target.centroid 按源议题规模加权时间衰减池化（w=|c|，规模越大偏向新向量越明显）
  - target.country_scope ← union；target.last_seen_at ← now()
  - target.lifecycle_state ← advance_for_size 推进（不后退）
  - 双方 revision_log 追加留痕（actor='machine'，trigger_evidence 含 sim）
  - 关联 agenda_events topic_id 从 c 迁回 target（事件本身保留历史）
  - 归并完成后对 target 触发增量重估（T3.13 reestimate_origin，详细设计 4.2
    算法 3 末段；target 无 AgendaEvent 时为正常空操作）

实体黑名单联动（详细设计 4.2 算法 5 用途②）：归并相似度门槛只看语义向量
（黑名单实体永远不提高相似度）；归并比对的关键词重叠在剔除 entity:blacklist
黑名单实体后计算，作为留痕写入 revision trigger_evidence 与 MergeDecision——
防止"共享超高频实体"被误读为归并依据。redis_client 缺位时黑名单不生效，
重叠按原始关键词计算并在留痕中标记 blacklist_applied=False（黑名单是优化
而非正确性依赖）。

绝不静默降级：每次归并/跳过均落 logger.info 结构化日志与 revision_log。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agenda_engine.config import get_agenda_settings
from app.agenda_engine.entity_blacklist import filter_blacklisted
from app.agenda_engine.lifecycle import advance_for_size, topic_size
from app.clustering.repository import assign_article, load_no_merge_pairs, norm_pair, time_decay_pool
from app.core.logging import get_logger
from app.models.agenda import AgendaEvent
from app.models.topic import Topic, TopicArticle

if TYPE_CHECKING:
    import redis

logger = get_logger("agenda_engine.merge")


@dataclass(frozen=True)
class MergeDecision:
    """单次成功归并决策留痕（合并方向：source → target，topic_id 复用 target）。"""

    source_topic_id: UUID
    target_topic_id: UUID
    similarity: float
    keyword_overlap: dict | None = None  # 黑名单剔除后的关键词重叠留痕（见 _keyword_overlap）
    llm_confirmed: bool | None = None   # LLM 语义确认是否通过（None=未启用/降级，纯向量判定）


@dataclass(frozen=True)
class MergeReport:
    """次日归并单轮报告：归并/保留/跳过全留痕，供 worker 观测与告警。"""

    merged: list[MergeDecision] = field(default_factory=list)
    new_topics: list[UUID] = field(default_factory=list)  # 未命中归并、保留新 topic_id 的候选
    skipped_no_merge: list[tuple[UUID, UUID]] = field(default_factory=list)  # 命中 no_merge_with 跳过
    skipped_locked: list[UUID] = field(default_factory=list)  # human_locked_fields 阻止归并的源议题
    skipped_llm: list[tuple[UUID, UUID]] = field(default_factory=list)  # LLM 判定非同一事件，保留独立议题


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（输入已 L2 归一化时退化为点积；零范数保 0 防 NaN）。"""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _size_weighted_pool(
    old: list[float],
    new: list[float],
    dt_hours: float,
    new_size: int,
    half_life_hours: float | None = None,
) -> list[float]:
    """按源议题规模加权的时间衰减池化。

    详细设计 4.2 算法 3：target.centroid ← time_decay_pool(target.centroid, c.centroid, w=|c|)
    直接修改 alpha：把 (1-alpha) 缩放为 (1-alpha) * w/(w+1)，使源议题规模越大
    越偏向新向量；w=1 时退化为普通 time_decay_pool；w→∞ 时新向量主导。

    推导：若把新向量重复 w 次再池化等价于加大新向量权重，但重复调用 time_decay_pool
    会改变衰减语义；改为直接放大 (1-alpha) 项更贴近伪代码意图并保持单次衰减。
    """
    if new_size < 1:
        return time_decay_pool(old, new, dt_hours, half_life_hours)
    settings_half = half_life_hours
    if settings_half is None:
        from app.clustering.config import get_cluster_settings
        settings_half = get_cluster_settings().centroid_half_life_hours
    alpha = 0.5 ** (max(dt_hours, 0.0) / settings_half)
    # 规模加权：w=1 → 1/2；w=2 → 2/3；w=9 → 9/10
    size_boost = new_size / (new_size + 1.0)
    new_weight = (1.0 - alpha) * size_boost
    # 归一化使得 alpha + new_weight ≤ 1，余项继续保留 old
    # 当 size_boost 较大时 new_weight 可能超过 (1-alpha)，整体重新归一
    total = alpha + new_weight
    if total <= 0.0:
        return list(old)
    alpha_norm = alpha / total
    new_norm = new_weight / total
    pooled = [alpha_norm * o + new_norm * n for o, n in zip(old, new, strict=True)]
    norm = math.sqrt(sum(v * v for v in pooled))
    if norm > 0:
        pooled = [v / norm for v in pooled]
    return pooled


def _next_seq(revision_log: list) -> int:
    """revision_log 追加序号（按既有条目数 +1，与详细设计 4.2 算法 3 口径一致）。"""
    return len(revision_log or []) + 1


def _append_revision(
    topic: Topic,
    *,
    field_name: str,
    after_value: str | None,
    trigger_evidence: dict,
    actor: str,
    actor_id: str | None = None,
    trigger: str | None = None,
    now: datetime | None = None,
) -> None:
    """向 topic.revision_log 追加一条留痕（字段口径与详细设计 2.7 对齐）。"""
    ts = (now or datetime.now(UTC)).isoformat()
    entry: dict = {
        "seq": _next_seq(topic.revision_log),
        "revised_at": ts,
        "field": field_name,
        "before_value": None,
        "after_value": after_value,
        "trigger_evidence": trigger_evidence,
        "actor": actor,
        "actor_id": actor_id,
        "model": None,
        "prompt_version": None,
    }
    if trigger:
        entry["trigger"] = trigger
    # revision_log 是 JSONB，整体替换以触发 SQLAlchemy 脏检查
    new_log = list(topic.revision_log or [])
    new_log.append(entry)
    topic.revision_log = new_log


def _keyword_overlap(
    candidate: Topic,
    target: Topic,
    redis_client: redis.Redis | None,
) -> dict:
    """归并比对的关键词重叠（剔除 entity:blacklist 黑名单实体后计算）。

    详细设计 4.2 算法 5 用途②：不因共享黑名单实体（超高频大国名/名人等超级节点）
    而提高归并判断中的实体重叠——重叠仅供留痕与审计，归并门槛仍只看语义向量。

    返回 dict：
      - shared_keywords：剔除黑名单后的共享关键词（升序，确定性输出）
      - overlap_count：共享数
      - blacklist_applied：黑名单是否生效（redis_client 缺位时 False，按原始关键词算）
      - filtered_out：被黑名单剔除的关键词（双方都算，升序）
    """
    cand_kw = [str(k) for k in (candidate.keywords or [])]
    tgt_kw = [str(k) for k in (target.keywords or [])]
    if redis_client is None:
        shared = sorted(set(cand_kw) & set(tgt_kw))
        return {
            "shared_keywords": shared,
            "overlap_count": len(shared),
            "blacklist_applied": False,
            "filtered_out": [],
        }
    filtered_cand = filter_blacklisted(cand_kw, redis_client)
    filtered_tgt = filter_blacklisted(tgt_kw, redis_client)
    shared = sorted(set(filtered_cand) & set(filtered_tgt))
    filtered_out = sorted(
        (set(cand_kw) | set(tgt_kw)) - (set(filtered_cand) | set(filtered_tgt))
    )
    return {
        "shared_keywords": shared,
        "overlap_count": len(shared),
        "blacklist_applied": True,
        "filtered_out": filtered_out,
    }


def _find_merge_target(
    db: Session,
    candidate: Topic,
    *,
    active_days: int,
    now: datetime | None = None,
) -> tuple[Topic, float] | None:
    """在档案集 D 内 HNSW 检索 candidate.centroid 最近邻 target。

    返回 (target, cosine_similarity)；找不到或向量缺失返回 None。
    now：活跃窗口时间基准（缺省墙钟 now）；回放注入模拟时间使历史议题可比。
    target 须不晚于 candidate 创建（first_seen_at <= candidate.first_seen_at）：
    保证"topic_id 复用"方向——新议题并入既有/更老议题，更老的 topic_id 存续，
    避免同窗新议题互并时首发议题 id 被新子簇吞没。
    """
    if candidate.centroid is None:
        return None
    cutoff = (now or datetime.now(UTC)) - timedelta(days=active_days)
    distance = Topic.centroid.cosine_distance([float(v) for v in candidate.centroid])
    stmt = (
        select(Topic, distance.label("distance"))
        .where(
            Topic.centroid.is_not(None),
            Topic.merged_into.is_(None),
            Topic.lifecycle_state != "archived",
            Topic.last_seen_at >= cutoff,
            Topic.first_seen_at <= candidate.first_seen_at,
            Topic.id != candidate.id,
        )
        .order_by(distance)
        .limit(1)
    )
    row = db.execute(stmt).first()
    if row is None:
        return None
    target, dist = row
    sim = 1.0 - float(dist)
    return target, sim


def _migrate_articles(db: Session, source: Topic, target: Topic) -> int:
    """把 source 下所有 topic_articles 迁往 target（assign_method='merge'，保留 weight）。

    已存在同 article 的 target 归属时：weight 取较大者，assign_method 改 'merge'。
    返回迁移的归属行数（含 upsert）。
    """
    stmt = select(TopicArticle).where(TopicArticle.topic_id == source.id)
    moved = 0
    for ta in db.scalars(stmt).all():
        weight = float(ta.weight)
        article_id = ta.article_id
        # 先删 source 行（释放 PK），再 upsert target 行
        db.delete(ta)
        db.flush()
        assign_article(db, target, article_id, weight, "merge")
        moved += 1
    return moved


def _migrate_agenda_events(db: Session, source: Topic, target: Topic) -> int:
    """把 source 关联的 agenda_events topic_id 改写为 target（事件本身保留历史快照）。"""
    stmt = select(AgendaEvent).where(AgendaEvent.topic_id == source.id)
    moved = 0
    for ev in db.scalars(stmt).all():
        ev.topic_id = target.id
        moved += 1
    if moved:
        db.flush()
    return moved


def merge_pair(
    db: Session,
    source: Topic,
    target: Topic,
    *,
    similarity: float,
    keyword_overlap: dict | None = None,
    now: datetime | None = None,
) -> None:
    """执行单次归并（不落 commit；调用方负责事务边界）。

    动作：c.merged_into=target.id；c.lifecycle_state='evolving'；topic_articles 迁移；
    target.centroid 规模加权池化；country_scope 并集；last_seen_at 推进；
    target.lifecycle_state 按规模推进（不后退）；双方 revision_log 追加；agenda_events 迁移。
    keyword_overlap：黑名单剔除后的关键词重叠留痕（nextday_merge 传入），并入 trigger_evidence。
    """
    ts = now or datetime.now(UTC)
    source_id = source.id
    target_id = target.id
    source_size = topic_size(db, source_id)

    # 1) 迁移文章归属
    moved_articles = _migrate_articles(db, source, target)

    # 2) target 质心规模加权池化
    if source.centroid is not None:
        src_vec = [float(v) for v in source.centroid]
        if target.centroid is not None:
            tgt_vec = [float(v) for v in target.centroid]
            dt_hours = (
                (ts - target.last_seen_at).total_seconds() / 3600.0
                if target.last_seen_at
                else 0.0
            )
            target.centroid = _size_weighted_pool(
                tgt_vec, src_vec, dt_hours, max(source_size, 1)
            )
        else:
            target.centroid = src_vec

    # 3) 国家集合并
    countries = set(target.country_scope or []) | set(source.country_scope or [])
    target.country_scope = sorted(countries)

    # 4) last_seen_at 推进
    target.last_seen_at = ts

    # 5) source 状态变更
    source.merged_into = target_id
    source.lifecycle_state = "evolving"

    # 6) target 生命周期按规模推进（不后退；evolving/archived 不被规模驱动覆盖）
    new_size = topic_size(db, target_id)
    target.lifecycle_state = advance_for_size(target.lifecycle_state, new_size)

    # 7) agenda_events 迁移
    moved_events = _migrate_agenda_events(db, source, target)

    # 8) 双方 revision_log 留痕
    _append_revision(
        source,
        field_name="merged_into",
        after_value=str(target_id),
        trigger_evidence={
            "similarity": round(similarity, 6),
            "algorithm": "nextday_merge",
            "moved_articles": moved_articles,
            "moved_events": moved_events,
            "keyword_overlap": keyword_overlap,
        },
        actor="machine",
        now=ts,
    )
    _append_revision(
        target,
        field_name="merged_from",
        after_value=str(source_id),
        trigger_evidence={
            "similarity": round(similarity, 6),
            "algorithm": "nextday_merge",
            "moved_articles": moved_articles,
            "moved_events": moved_events,
            "keyword_overlap": keyword_overlap,
        },
        actor="machine",
        now=ts,
    )

    db.flush()
    logger.info(
        "nextday_merge_pair_done",
        source_id=str(source_id),
        target_id=str(target_id),
        similarity=round(similarity, 6),
        moved_articles=moved_articles,
        moved_events=moved_events,
        source_size=source_size,
        target_size=new_size,
    )


def nextday_merge(
    db: Session,
    *,
    candidate_since: datetime | None = None,
    batch_size: int | None = None,
    redis_client: redis.Redis | None = None,
    now: datetime | None = None,
    llm_annotator: Any = None,
) -> MergeReport:
    """次日归并主入口（flush 由本函数完成；commit 由调用方负责）。

    候选集 C：merged_into IS NULL AND lifecycle_state IN ('nascent','forming')
              （详细设计 4.2 算法 3："昨日至今的新议题/微簇集"——forming 是
              在线归簇已获同伴的新议题，同属 C；仅曾实现为 nascent 导致
              forming↔forming 同事件子簇永不归并，M5 回放误拆根因之一）
              AND first_seen_at >= candidate_since（默认近 24h，估算）
              按 first_seen_at 升序，老议题优先被并入目标
    档案集 D：merged_into IS NULL AND lifecycle_state != 'archived'
              AND last_seen_at >= now() - merge_active_days
    redis_client：提供时归并比对的关键词重叠剔除 entity:blacklist 黑名单实体后计算
              （详细设计 4.2 算法 5 用途②）；缺位时按原始关键词算并标记
              blacklist_applied=False（黑名单是优化而非正确性依赖）
    now：本轮归并的时间基准（缺省墙钟 now）；回放注入模拟时间（如日界时刻），
         使历史时间轴上的议题在自身时间轴内参与归并比对
    llm_annotator：TopicAnnotator（依赖注入）。提供且未降级时，对向量命中阈值的
         候选做 LLM 语义确认（merge_confirm：是否同一事件）；same_event=False
         则跳过归并。None 或降级时回退纯向量阈值（行为与现状等价）。

    单轮内迭代至不动点：每次归并更新 target 质心后重评剩余低于阈值候选，
    消除单遍顺序伪影（先评估差之毫厘、同轮质心推高后无可追轮的误拆）。
    """
    settings = get_agenda_settings()
    now = now or datetime.now(UTC)
    since = candidate_since or (now - timedelta(hours=24))
    limit = batch_size or settings.merge_batch_size
    merge_sim = settings.merge_sim
    active_days = settings.merge_active_days

    no_merge_pairs = load_no_merge_pairs(db)

    # 候选集（昨日至今的新议题/微簇：nascent 孤证 + forming 已形成同伴的新议题）
    stmt = (
        select(Topic)
        .where(
            Topic.merged_into.is_(None),
            Topic.lifecycle_state.in_(("nascent", "forming")),
            Topic.first_seen_at >= since,
        )
        .order_by(Topic.first_seen_at.asc())
        .limit(limit)
    )
    candidates = list(db.scalars(stmt).all())

    merged: list[MergeDecision] = []
    new_topics: list[UUID] = []
    skipped_no_merge: list[tuple[UUID, UUID]] = []
    skipped_locked: list[UUID] = []
    skipped_llm: list[tuple[UUID, UUID]] = []

    # 单轮内迭代至不动点：候选按 first_seen_at 升序逐轮评估；每次归并会更新
    # target 质心（规模加权时间衰减池化），使"先评估时略低于阈值、同轮后续归并
    # 推高相似度"的候选在下一轮追上——消除单遍顺序伪影（M5 回放误拆根因之三：
    # russia-ukraine a2 同轮先评估 0.608<0.62 未并，同轮主簇合并 4 篇后
    # sim 升至 0.629 却无下一轮可追）。
    # 终止性：每轮至少归并 1 个候选才继续，候选数有限故必然终止。
    # 锁定/无向量/无目标/命中 no_merge_with 为终态决策，首轮判定后不再重评。
    pending = list(candidates)
    while pending:
        retry: list[Topic] = []
        progress = False
        for cand in pending:
            # 人工锁定 merged_into 的源议题不自动归并（人工优先）
            locked = cand.human_locked_fields or []
            if "merged_into" in locked:
                skipped_locked.append(cand.id)
                logger.info(
                    "nextday_merge_skip_locked",
                    candidate_id=str(cand.id), locked_fields=locked,
                )
                continue

            # 无向量 → 无比对基础，保留新 topic_id
            if cand.centroid is None:
                new_topics.append(cand.id)
                logger.info(
                    "nextday_merge_skip_no_centroid",
                    candidate_id=str(cand.id),
                )
                continue

            found = _find_merge_target(db, cand, active_days=active_days, now=now)
            if found is None:
                new_topics.append(cand.id)
                continue
            target, sim = found

            # 相似度低于阈值：本轮保留；target 质心被同轮其他归并推高后下轮重评
            if sim < merge_sim:
                retry.append(cand)
                logger.info(
                    "nextday_merge_below_threshold",
                    candidate_id=str(cand.id),
                    target_id=str(target.id),
                    similarity=round(sim, 6),
                    threshold=merge_sim,
                )
                continue

            # 命中 no_merge_with 名单：跳过自动归并，保留独立 topic_id
            pair = norm_pair(cand.id, target.id)
            if pair in no_merge_pairs:
                skipped_no_merge.append((cand.id, target.id))
                new_topics.append(cand.id)
                logger.info(
                    "nextday_merge_skip_no_merge_list",
                    candidate_id=str(cand.id),
                    target_id=str(target.id),
                    similarity=round(sim, 6),
                )
                continue

            # 执行归并（单源议题事务内由本函数 flush，调用方统一 commit）
            # LLM 语义确认（T3.3 增强）：向量命中阈值后，若注入 llm_annotator 且未降级，
            # 让 LLM 判断两簇是否同一事件。**仅高置信否定才拦截**（防止 LLM 对跨语言/
            # 多角度改写保守误拒，破坏本应归并的同事件对）；medium/low 或 same_event=True
            # 均维持向量判定（向量已 ≥threshold 确认相似，宁可不漏并）。
            llm_confirmed: bool | None = None
            if llm_annotator is not None:
                from app.agenda_engine.merge_confirm import confirm_same_event

                confirm = confirm_same_event(db, cand, target, llm_annotator)
                if confirm is not None:
                    llm_confirmed = bool(confirm.same_event)
                    if not confirm.same_event and confirm.confidence == "high":
                        # LLM 高置信判定非同一事件：跳过归并，保留独立 topic_id
                        skipped_llm.append((cand.id, target.id))
                        new_topics.append(cand.id)
                        logger.info(
                            "nextday_merge_llm_reject",
                            candidate_id=str(cand.id),
                            target_id=str(target.id),
                            similarity=round(sim, 6),
                            confidence=confirm.confidence,
                            reasoning=confirm.reasoning,
                        )
                        continue

            # 关键词重叠：剔除黑名单实体后计算（留痕与审计用，门槛仍只看语义向量）
            overlap_info = _keyword_overlap(cand, target, redis_client)
            merge_pair(db, cand, target, similarity=sim, keyword_overlap=overlap_info, now=now)
            merged.append(MergeDecision(
                source_topic_id=cand.id,
                target_topic_id=target.id,
                similarity=sim,
                keyword_overlap=overlap_info,
                llm_confirmed=llm_confirmed,
            ))
            progress = True

            # 归并完成后触发受影响议题的增量重估（详细设计 4.2 算法 3 末段：
            # "merge_map 非空 → 触发受影响议题的增量重估（见算法 4）"）。
            # target 无 AgendaEvent 时 reestimate_origin 直接返回 None（正常路径）。
            # 函数级 import：保持 merge → revision 单向依赖，避免模块加载顺序耦合。
            from app.agenda_engine.revision import reestimate_origin

            reestimate_origin(
                db,
                target.id,
                trigger={
                    "type": "merge",
                    "source_topic_id": str(cand.id),
                    "target_topic_id": str(target.id),
                    "similarity": round(sim, 6),
                },
            )

        if not progress:
            # 不动点：剩余候选全部低于阈值，保留新 topic_id
            new_topics.extend(c.id for c in retry)
            break
        pending = retry

    db.flush()
    logger.info(
        "nextday_merge_done",
        candidates=len(candidates),
        merged=len(merged),
        new_topics=len(new_topics),
        skipped_no_merge=len(skipped_no_merge),
        skipped_locked=len(skipped_locked),
        skipped_llm=len(skipped_llm),
    )
    return MergeReport(
        merged=merged,
        new_topics=new_topics,
        skipped_no_merge=skipped_no_merge,
        skipped_locked=skipped_locked,
        skipped_llm=skipped_llm,
    )
