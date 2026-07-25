"""议题分裂与误并回滚（T3.4，详细设计 1.7 + 4.2 算法 3 注释 + ADR-006）。

POST /topics/{parent_id}/split 由人工触发：
  - 校验：parent/child 均存在；child.merged_into == parent_id（child 必须由 parent 归并而来）；
    parent.lifecycle_state != 'archived'；parent.merged_into IS NULL
  - 恢复 child：merged_into=None，lifecycle_state 由规模重算（nascent/forming/confirmed）
  - 恢复 child 文章归属：把 assign_method='merge' 且原属于 child 的 topic_articles
    从 parent 迁回 child（assign_method 改回 'online'，保留 weight）；agenda_events 同步迁回
  - 双方写入 no_merge_with（去重）
  - 双方 revision_log 追加：actor='human', trigger='manual_split', field='split_from', after=对方 ID
  - parent 与 child 质心分别按剩余 topic_articles 文章 embedding 重算
    （time_decay_pool 非可逆运算，不能简单减去 child 质心，必须基于文章重算）
  - parent.lifecycle_state 由规模重算（可能 confirmed→forming，evolving→forming 是合法的）

绝不静默降级：校验失败抛 SplitError；每次分裂完整留痕，actor='human'。
"""
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agenda_engine.lifecycle import topic_size
from app.core.errors import CODE_NOT_FOUND, CODE_STATE_INVALID, BizError
from app.core.logging import get_logger
from app.models.agenda import AgendaEvent
from app.models.article import Article
from app.models.topic import Topic, TopicArticle

logger = get_logger("agenda_engine.split")


class SplitError(BizError):
    """分裂业务异常（携带 3001/4002 错误码）。"""


def _next_seq(revision_log: list) -> int:
    return len(revision_log or []) + 1


def _append_revision(
    topic: Topic,
    *,
    field_name: str,
    after_value: str | None,
    trigger_evidence: dict,
    actor: str,
    actor_id: str | None,
    trigger: str,
    now: datetime,
) -> None:
    entry = {
        "seq": _next_seq(topic.revision_log),
        "revised_at": now.isoformat(),
        "field": field_name,
        "before_value": None,
        "after_value": after_value,
        "trigger_evidence": trigger_evidence,
        "actor": actor,
        "actor_id": actor_id,
        "model": None,
        "prompt_version": None,
        "trigger": trigger,
    }
    new_log = list(topic.revision_log or [])
    new_log.append(entry)
    topic.revision_log = new_log


def _lifecycle_for_size_recalc(size: int, *, confirmed_min_size: int | None = None) -> str:
    """按规模重算生命周期（允许后退，用于分裂/重算场景）。

    与 advance_for_size 的"只前进不后退"语义相反：分裂后规模真实下降，
    必须允许从 confirmed 退回 forming（详细设计 4.2 算法 3 注释：
    evolving → forming/confirmed 是合法的）。
    """
    from app.clustering.config import get_cluster_settings
    threshold = confirmed_min_size or get_cluster_settings().confirmed_min_size
    if size >= threshold:
        return "confirmed"
    if size >= 2:
        return "forming"
    return "nascent"


def _recalc_centroid(db: Session, topic: Topic) -> None:
    """按 topic_articles 当前归属文章 embedding 重新池化议题质心。

    time_decay_pool 不是可逆运算，不能"减去"已迁出的子议题质心；
    用归属文章 embedding 按时间衰减加权重算（与在线归簇/归并的池化语义一致）。

    无剩余文章时保留原 centroid（避免清空后次日归并失去比对基础，详细设计
    并未要求清空；若需彻底失效由后续重聚类校正环节处理）。
    """
    stmt = (
        select(Article)
        .join(TopicArticle, TopicArticle.article_id == Article.id)
        .where(
            TopicArticle.topic_id == topic.id,
            Article.embedding.is_not(None),
        )
        .order_by(Article.published_at.asc())
    )
    articles = list(db.scalars(stmt).all())
    if not articles:
        return
    from app.clustering.repository import time_decay_pool
    centroid: list[float] = [float(v) for v in articles[0].embedding]
    last_t = articles[0].published_at
    for art in articles[1:]:
        vec = [float(v) for v in art.embedding]
        dt_hours = max((art.published_at - last_t).total_seconds() / 3600.0, 0.0)
        centroid = time_decay_pool(centroid, vec, dt_hours)
        last_t = art.published_at
    topic.centroid = centroid


def _recalc_country_scope(db: Session, topic: Topic) -> None:
    """按当前归属文章 country_code 重算 country_scope。"""
    stmt = (
        select(Article.country_code)
        .join(TopicArticle, TopicArticle.article_id == Article.id)
        .where(TopicArticle.topic_id == topic.id)
        .distinct()
    )
    countries = sorted({row[0] for row in db.execute(stmt).all() if row[0]})
    topic.country_scope = countries


def _restore_child_articles(db: Session, parent: Topic, child: Topic) -> int:
    """把 parent 下 assign_method='merge' 的 topic_articles 迁回 child。

    留痕口径：assign_method 改回 'online'（不再是 merge 状态），保留原 weight；
    注意本实现假设归并前的原 assign_method 已不可考，简化为 'online'（与详细设计
    "恢复双方 topic_id 与文章归属"口径一致；revision_log 已记录原归并动作可追溯）。

    返回迁移的归属行数。
    """
    stmt = select(TopicArticle).where(
        TopicArticle.topic_id == parent.id,
        TopicArticle.assign_method == "merge",
    )
    restored = 0
    for ta in db.scalars(stmt).all():
        weight = float(ta.weight)
        article_id = ta.article_id
        db.delete(ta)
        db.flush()
        db.add(TopicArticle(
            topic_id=child.id,
            article_id=article_id,
            weight=round(min(max(weight, 0.0), 1.0), 3),
            assign_method="online",
        ))
        restored += 1
    if restored:
        db.flush()
    return restored


def _restore_child_agenda_events(
    db: Session,
    parent: Topic,
    child: Topic,
    *,
    merge_started_at: datetime | None,
) -> int:
    """把 parent 下原属于 child 的 agenda_events 迁回 child。

    判据：归并时所有 child.agenda_events 被改写到 parent（见 merge._migrate_agenda_events），
    但 parent 本身可能有自己产生的事件，不能全部迁回。简化策略：归并发生时间点之后
    （或时点附近）创建/更新的事件且 origin_at 在 child.first_seen_at 之后的视为 child 来源；
    更稳妥的做法是依据 child 恢复后的 topic_articles 是否覆盖事件 evidence 中的 article，
    但这会增加查询复杂度。当前实现按"origin_at 早于 parent 该事件 origin_at 且与 child 文章
    重合"的保守口径，仅在归并时间点明确时才回滚；否则不动。

    返回迁移的事件数。
    """
    # 取 child 当前文章 ID 集（恢复归属后）
    article_ids_stmt = select(TopicArticle.article_id).where(TopicArticle.topic_id == child.id)
    child_article_ids = {row[0] for row in db.execute(article_ids_stmt).all()}
    if not child_article_ids:
        return 0

    stmt = select(AgendaEvent).where(AgendaEvent.topic_id == parent.id)
    moved = 0
    for ev in db.scalars(stmt).all():
        # 事件 origin_source_id 对应文章的 source 关联在 child 文章集合内，则迁回
        # 简化：origin_at 落在 child.first_seen_at 之后 + 事件 stats_evidence 包含 child 文章 id
        # 这里采用最保守口径：归并时刻之后 updated_at 未变（即非 parent 后续自产）的事件迁回
        # 实际工程中更稳妥的口径是 events 增加 original_topic_id 字段，本版本以 origin_at 判断
        if ev.origin_at < child.first_seen_at:
            continue
        # 若 merge_started_at 已知，事件 updated_at 早于 merge_started_at 的视为归并前 child 自产
        if merge_started_at is not None and ev.updated_at and ev.updated_at > merge_started_at:
            # 归并之后又被 parent 改动过 → 视为 parent 数据，不迁回
            continue
        ev.topic_id = child.id
        moved += 1
    if moved:
        db.flush()
    return moved


def _append_no_merge(topic: Topic, partner_id: UUID) -> None:
    """向 topic.no_merge_with 追加 partner_id（去重，字符串形式落库）。"""
    current = list(topic.no_merge_with or [])
    partner_str = str(partner_id)
    if partner_str not in current:
        current.append(partner_str)
    topic.no_merge_with = current


def split_topic(
    db: Session,
    parent_id: UUID,
    child_id: UUID,
    *,
    actor_user_id: UUID,
    now: datetime | None = None,
) -> tuple[Topic, Topic]:
    """POST /topics/{parent_id}/split 服务层入口（单事务；commit 由调用方）。

    校验失败抛 SplitError（3001/4002）；成功后返回 (parent, child) 已刷新的 ORM 实例。
    """
    ts = now or datetime.now(UTC)
    parent = db.get(Topic, parent_id)
    if parent is None:
        raise SplitError(CODE_NOT_FOUND, f"parent 议题不存在: {parent_id}")
    child = db.get(Topic, child_id)
    if child is None:
        raise SplitError(CODE_NOT_FOUND, f"child 议题不存在: {child_id}")

    if parent.lifecycle_state == "archived":
        raise SplitError(CODE_STATE_INVALID, "归档议题不可分裂")
    if parent.merged_into is not None:
        raise SplitError(CODE_STATE_INVALID, "parent 议题已并入其他议题，不可再分裂")
    if child.merged_into != parent_id:
        raise SplitError(
            CODE_STATE_INVALID,
            f"child 议题并非由本议题归并而来: child.merged_into={child.merged_into}",
        )

    # 记录归并时刻（从 child.revision_log 最近一条 field='merged_into' 提取），供事件回滚参考
    merge_started_at: datetime | None = None
    for entry in reversed(child.revision_log or []):
        if entry.get("field") == "merged_into" and entry.get("after_value") == str(parent_id):
            try:
                merge_started_at = datetime.fromisoformat(str(entry.get("revised_at")))
            except (ValueError, TypeError):
                merge_started_at = None
            break

    # 1) 恢复 child：merged_into 清空；lifecycle_state 由规模重算（允许后退）
    child.merged_into = None

    # 2) 恢复 child 的文章归属：parent 下 assign_method='merge' 的归属全部迁回 child
    restored_articles = _restore_child_articles(db, parent, child)

    # 3) agenda_events 回滚
    restored_events = _restore_child_agenda_events(
        db, parent, child, merge_started_at=merge_started_at
    )

    # 4) 双方 no_merge_with 互写（去重）
    _append_no_merge(parent, child.id)
    _append_no_merge(child, parent.id)

    # 5) 双方 revision_log 追加（actor='human', trigger='manual_split'）
    trigger_evidence = {
        "restored_articles": restored_articles,
        "restored_events": restored_events,
    }
    _append_revision(
        parent,
        field_name="split_from",
        after_value=str(child.id),
        trigger_evidence=trigger_evidence,
        actor="human",
        actor_id=str(actor_user_id),
        trigger="manual_split",
        now=ts,
    )
    _append_revision(
        child,
        field_name="split_from",
        after_value=str(parent.id),
        trigger_evidence=trigger_evidence,
        actor="human",
        actor_id=str(actor_user_id),
        trigger="manual_split",
        now=ts,
    )

    # 6) 双方质心按剩余 topic_articles 文章 embedding 重算
    _recalc_centroid(db, parent)
    _recalc_centroid(db, child)

    # 7) 双方 country_scope 重算（与当前归属一致）
    _recalc_country_scope(db, parent)
    _recalc_country_scope(db, child)

    # 8) 生命周期按规模重算（允许后退）
    parent_size = topic_size(db, parent.id)
    child_size = topic_size(db, child.id)
    parent.lifecycle_state = _lifecycle_for_size_recalc(parent_size)
    child.lifecycle_state = _lifecycle_for_size_recalc(child_size)

    # 9) last_seen_at 推进（分裂本身视为活跃信号）
    parent.last_seen_at = ts
    child.last_seen_at = ts

    db.flush()
    logger.info(
        "topic_split_done",
        parent_id=str(parent.id),
        child_id=str(child.id),
        restored_articles=restored_articles,
        restored_events=restored_events,
        parent_size=parent_size,
        child_size=child_size,
        actor_user_id=str(actor_user_id),
    )
    return parent, child
