"""次日自动归并（T3.3，详细设计 4.2 算法 3 + ADR-006 自我纠错）。

候选集 C：merged_into IS NULL AND lifecycle_state='nascent'（孤立微簇）
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
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agenda_engine.config import get_agenda_settings
from app.agenda_engine.entity_blacklist import filter_blacklisted
from app.agenda_engine.lifecycle import advance_for_size, topic_size
from app.clustering.repository import assign_article, time_decay_pool
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


@dataclass(frozen=True)
class MergeReport:
    """次日归并单轮报告：归并/保留/跳过全留痕，供 worker 观测与告警。"""

    merged: list[MergeDecision] = field(default_factory=list)
    new_topics: list[UUID] = field(default_factory=list)  # 未命中归并、保留新 topic_id 的候选
    skipped_no_merge: list[tuple[UUID, UUID]] = field(default_factory=list)  # 命中 no_merge_with 跳过
    skipped_locked: list[UUID] = field(default_factory=list)  # human_locked_fields 阻止归并的源议题


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


def _load_no_merge_pairs(db: Session) -> set[tuple[UUID, UUID]]:
    """∪ topics.no_merge_with 双向展开为无序对集合（frozenset 语义）。

    返回 set of (min_id, max_id) 元组（按 UUID 字节序规范化），便于 O(1) 查 (c, target)。
    """
    stmt = select(Topic.id, Topic.no_merge_with).where(Topic.no_merge_with.is_not(None))
    pairs: set[tuple[UUID, UUID]] = set()
    for tid, partners in db.execute(stmt).all():
        if not partners:
            continue
        for partner_raw in partners:
            try:
                partner = UUID(str(partner_raw))
            except (ValueError, TypeError):
                continue
            pair = (tid, partner) if tid.bytes <= partner.bytes else (partner, tid)
            pairs.add(pair)
    return pairs


def _norm_pair(a: UUID, b: UUID) -> tuple[UUID, UUID]:
    return (a, b) if a.bytes <= b.bytes else (b, a)


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
) -> tuple[Topic, float] | None:
    """在档案集 D 内 HNSW 检索 candidate.centroid 最近邻 target。

    返回 (target, cosine_similarity)；找不到或向量缺失返回 None。
    """
    if candidate.centroid is None:
        return None
    cutoff = datetime.now(UTC) - timedelta(days=active_days)
    distance = Topic.centroid.cosine_distance([float(v) for v in candidate.centroid])
    stmt = (
        select(Topic, distance.label("distance"))
        .where(
            Topic.centroid.is_not(None),
            Topic.merged_into.is_(None),
            Topic.lifecycle_state != "archived",
            Topic.last_seen_at >= cutoff,
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
) -> MergeReport:
    """次日归并主入口（flush 由本函数完成；commit 由调用方负责）。

    候选集 C：merged_into IS NULL AND lifecycle_state='nascent' AND first_seen_at >= since
              按 first_seen_at 升序，老议题优先被并入目标
    档案集 D：merged_into IS NULL AND lifecycle_state != 'archived'
              AND last_seen_at >= now() - merge_active_days
    redis_client：提供时归并比对的关键词重叠剔除 entity:blacklist 黑名单实体后计算
              （详细设计 4.2 算法 5 用途②）；缺位时按原始关键词算并标记
              blacklist_applied=False（黑名单是优化而非正确性依赖）
    """
    settings = get_agenda_settings()
    now = datetime.now(UTC)
    since = candidate_since or (now - timedelta(hours=24))
    limit = batch_size or settings.merge_batch_size
    merge_sim = settings.merge_sim
    active_days = settings.merge_active_days

    no_merge_pairs = _load_no_merge_pairs(db)

    # 候选集（孤立微簇）
    stmt = (
        select(Topic)
        .where(
            Topic.merged_into.is_(None),
            Topic.lifecycle_state == "nascent",
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

    for cand in candidates:
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

        found = _find_merge_target(db, cand, active_days=active_days)
        if found is None:
            new_topics.append(cand.id)
            continue
        target, sim = found

        # 相似度低于阈值：保留新 topic_id
        if sim < merge_sim:
            new_topics.append(cand.id)
            logger.info(
                "nextday_merge_below_threshold",
                candidate_id=str(cand.id),
                target_id=str(target.id),
                similarity=round(sim, 6),
                threshold=merge_sim,
            )
            continue

        # 命中 no_merge_with 名单：跳过自动归并，保留独立 topic_id
        pair = _norm_pair(cand.id, target.id)
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
        # 关键词重叠：剔除黑名单实体后计算（留痕与审计用，门槛仍只看语义向量）
        overlap_info = _keyword_overlap(cand, target, redis_client)
        merge_pair(db, cand, target, similarity=sim, keyword_overlap=overlap_info, now=now)
        merged.append(MergeDecision(
            source_topic_id=cand.id,
            target_topic_id=target.id,
            similarity=sim,
            keyword_overlap=overlap_info,
        ))

    db.flush()
    logger.info(
        "nextday_merge_done",
        candidates=len(candidates),
        merged=len(merged),
        new_topics=len(new_topics),
        skipped_no_merge=len(skipped_no_merge),
        skipped_locked=len(skipped_locked),
    )
    return MergeReport(
        merged=merged,
        new_topics=new_topics,
        skipped_no_merge=skipped_no_merge,
        skipped_locked=skipped_locked,
    )
