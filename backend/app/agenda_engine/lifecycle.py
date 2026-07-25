"""议题生命周期状态机完整版（T3.2，详细设计 2.7 / 4.2 算法 3 注释）。

五态：nascent（孤证微簇）→ forming（有同伴）→ confirmed（达确认规模）→
evolving（合并/分裂中，由归并与人工分裂流程维护）→ archived（消亡，保留可查）。

推进规则：
- 规模推进只前进不后退（nascent → forming → confirmed），由
  clustering.repository.update_topic_on_assignment 在归入新文章时维护
- evolving 由次日归并（T3.3）与人工分裂（T3.4）显式进入/退出，不由规模驱动
- archived 由本模块 sweeper 维护：连续 archive_after_days 天（默认 7，估算）
  无新报道（last_seen_at 超窗）自动归档；归档不物理删除，文章归属与历史
  revision_log 全保留可查
- 人工锁定字段（human_locked_fields）非空议题不自动归档（尊重人工结论）

归档边界：merged_into 非空（已并入其他议题）的源议题由归并流程负责置 evolving，
本 sweeper 不再触碰；archived 议题若再次获得新文章（在线归簇会先把 archived
排除在比对池外），由重聚类校正环节决定是否复活——本状态机不做"复活"自动动作。
"""
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agenda_engine.config import get_agenda_settings
from app.core.logging import get_logger
from app.models.topic import Topic, TopicArticle

logger = get_logger("agenda.lifecycle")

LIFECYCLE_ORDER = ("nascent", "forming", "confirmed")


def can_transition(current: str, target: str) -> bool:
    """校验生命周期流转是否合法（不完整状态机：只允许文档定义的转移）。

    合法转移：
      nascent → forming / archived
      forming → confirmed / archived
      confirmed → evolving / archived
      evolving → forming / confirmed / archived（归并拆分后回到规模驱动）
      archived → (终态，不允许自动转移；复活走重聚类显式流程)
    """
    allowed: dict[str, set[str]] = {
        "nascent": {"forming", "archived"},
        "forming": {"confirmed", "archived"},
        "confirmed": {"evolving", "archived"},
        "evolving": {"forming", "confirmed", "archived"},
        "archived": set(),
    }
    return target in allowed.get(current, set())


def advance_for_size(current: str, size: int, confirmed_min_size: int | None = None) -> str:
    """按规模推进生命周期（只前进不后退；evolving/archived 不被规模驱动覆盖）。"""
    threshold = confirmed_min_size or get_agenda_settings().confirmed_min_size
    if current in ("evolving", "archived"):
        return current
    if size >= threshold:
        target = "confirmed"
    elif size >= 2:
        target = "forming"
    else:
        target = "nascent"
    order = LIFECYCLE_ORDER
    if current in order and order.index(target) > order.index(current):
        return target
    return current


def sweep_archived(
    db: Session,
    *,
    archive_after_days: int | None = None,
    now: datetime | None = None,
) -> list[UUID]:
    """消亡扫描：连续 N 天无新报道的活跃议题自动归档。

    扫描范围：merged_into IS NULL 且 lifecycle_state != 'archived' 的议题；
    跳过：human_locked_fields 非空（人工锁定议题不自动消亡，等人工处置）。
    返回本次归档的议题 ID 列表（用于告警与观测）。
    """
    settings = get_agenda_settings()
    days = archive_after_days or settings.lifecycle_archive_days
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=days)

    stmt = select(Topic).where(
        Topic.merged_into.is_(None),
        Topic.lifecycle_state != "archived",
        Topic.last_seen_at < cutoff,
    )
    archived: list[UUID] = []
    for topic in db.scalars(stmt).all():
        if topic.human_locked_fields:
            logger.info(
                "lifecycle_archive_skip_locked",
                topic_id=str(topic.id), locked=topic.human_locked_fields,
            )
            continue
        if not can_transition(topic.lifecycle_state, "archived"):
            logger.warning(
                "lifecycle_archive_illegal_transition",
                topic_id=str(topic.id), current=topic.lifecycle_state,
            )
            continue
        old_state = topic.lifecycle_state
        topic.lifecycle_state = "archived"
        archived.append(topic.id)
        logger.info(
            "lifecycle_archived",
            topic_id=str(topic.id), from_state=old_state,
            last_seen_at=topic.last_seen_at.isoformat() if topic.last_seen_at else None,
            cutoff_days=days,
        )
    if archived:
        db.flush()
    return archived


def active_topic_ids(db: Session) -> list[UUID]:
    """活跃议题 ID（未归档且未并入其他议题）；供归并/快照/黑名单比对过滤。"""
    stmt = select(Topic.id).where(
        Topic.merged_into.is_(None),
        Topic.lifecycle_state != "archived",
    )
    return list(db.scalars(stmt).all())


def topic_size(db: Session, topic_id: UUID) -> int:
    """议题当前归属文章数（含 related_docs 折叠，归属即权重）。"""
    from sqlalchemy import func
    return int(
        db.scalar(
            select(func.count()).select_from(TopicArticle).where(TopicArticle.topic_id == topic_id)
        ) or 0
    )
