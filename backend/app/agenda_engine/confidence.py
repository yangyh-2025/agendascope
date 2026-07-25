"""置信度自动升降（T3.14）与修正风暴保护（详细设计 4.2 算法 4 注释）。

职责：
- maybe_escalate / maybe_deescalate：基于事件当前 origin_type / origin_confidence /
  follower_sequence / detection_method / stats_evidence 自动推进 confidence
  （watching → suspected；suspected → confirmed 只能由人工 POST /agenda-events/{id}/confirm
  推进，本模块不自动跨档到 confirmed——见详细设计 4.2 算法 4 + 3.2 流程图）
- check_revision_storm：单议题 revision_storm_window_hours 小时内 revision_log 中
  actor='machine' 的修正数 > revision_storm_threshold 时，冻结自动修正
  （human_locked_fields 锁定全部 origin_* 字段）并写 alerts 表 P1 告警转人工复核

不变量（与详细设计 3.2 对齐）：
- 机器永不推翻人工：human_locked_fields 非空字段本模块不修改
- 撤销（任意 → watching）：origin_confidence 降 'low' 或 follower_sequence 清空时触发
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.agenda_engine.config import AgendaSettings, get_agenda_settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.models.agenda import AgendaEvent

logger = get_logger("agenda_engine.confidence")

# 修正风暴触发时锁定的字段集合（覆盖 origin/follower/stats 全部 origin_* 链路）
# 锁定后 reestimate_origin 不再自动推翻这些字段，必须人工处置
STORM_LOCKED_FIELDS: tuple[str, ...] = (
    "origin_type",
    "origin_country_code",
    "origin_source_id",
    "origin_entity_id",
    "origin_at",
    "origin_confidence",
    "origin_quote",
    "follower_sequence",
    "stats_evidence",
    "detection_method",
    "confidence",
    "status",
)


def _stats_significant(event: AgendaEvent) -> bool:
    """stats_evidence 是否显著（任一检验 significant=True 即认为统计上支持）。"""
    stats = event.stats_evidence or {}
    if not isinstance(stats, dict):
        return False
    for key in ("xcorr", "granger", "qap"):
        block = stats.get(key)
        if isinstance(block, dict) and block.get("significant") is True:
            return True
    return False


def _escalation_condition_met(event: AgendaEvent) -> bool:
    """watching → suspected 的全部必要条件（详细设计 4.2 算法 4 + 任务说明）：

    1. 至少一个 origin_type 已确定（media/person/org，即 origin_type 非空）
    2. origin_confidence ∈ ('medium', 'high')
    3. follower_sequence 至少 1 个国家
    4. detection_method != 'media_time_fallback' OR stats_evidence 显著
    """
    if not event.origin_type:
        return False
    if event.origin_type not in ("media", "person", "org"):
        return False
    if event.origin_confidence not in ("medium", "high"):
        return False
    followers = event.follower_sequence or []
    if not followers:
        return False
    return not (event.detection_method == "media_time_fallback" and not _stats_significant(event))


def maybe_escalate(
    db: Session,
    event: AgendaEvent,
    *,
    settings: AgendaSettings | None = None,
) -> bool:
    """根据事件当前字段尝试把 confidence 从 watching 升到 suspected。

    返回 True 表示发生了升级；否则 False。
    仅在 confidence='watching' 且满足全部升级条件时推进；suspected → confirmed
    由人工 POST /agenda-events/{id}/confirm 触发，本函数不自动跨档到 confirmed。
    """
    _ = settings or get_agenda_settings()
    if event.confidence != "watching":
        return False
    if not _escalation_condition_met(event):
        return False
    event.confidence = "suspected"
    db.flush()
    logger.info(
        "confidence_escalated",
        event_id=str(event.id),
        from_confidence="watching",
        to_confidence="suspected",
        origin_type=event.origin_type,
        origin_confidence=event.origin_confidence,
        follower_count=len(event.follower_sequence or []),
        detection_method=event.detection_method,
        stats_significant=_stats_significant(event),
    )
    return True


def maybe_deescalate(
    db: Session,
    event: AgendaEvent,
    *,
    settings: AgendaSettings | None = None,
) -> bool:
    """origin_confidence 降 'low' 或 follower_sequence 清空时，把 confidence 撤销回 watching。

    返回 True 表示发生了降级；否则 False。
    confirmed 不自动降级（人工结论机器不推翻，与 human_locked_fields 同语义）。
    """
    _ = settings or get_agenda_settings()
    # 人工已 confirmed：机器不自动降级（人工否决优先，详细设计 3.2 关键不变量）
    if event.confidence == "confirmed":
        return False
    if event.confidence == "watching":
        return False
    should_drop = event.origin_confidence == "low" or not (event.follower_sequence or [])
    if not should_drop:
        return False
    old = event.confidence
    event.confidence = "watching"
    db.flush()
    logger.info(
        "confidence_deescalated",
        event_id=str(event.id),
        from_confidence=old,
        to_confidence="watching",
        origin_confidence=event.origin_confidence,
        follower_count=len(event.follower_sequence or []),
    )
    return True


def _count_recent_machine_revisions(
    event: AgendaEvent,
    *,
    window_hours: int,
    now: datetime | None = None,
) -> int:
    """统计 event.revision_log 中窗口内 actor='machine' 的修正条数。

    口径：revision_log 是 JSONB 数组，单条含 revised_at ISO8601 字符串；
    按 window_hours 过滤（默认 24h）；超窗的不计入风暴阈值。
    """
    ts_now = now or datetime.now(UTC)
    cutoff = ts_now - timedelta(hours=window_hours)
    count = 0
    for entry in event.revision_log or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("actor") != "machine":
            continue
        # 被人工否决的条目仍计入窗口（它们仍是机器产生的修正尝试）
        revised_at_raw = entry.get("revised_at")
        if not revised_at_raw:
            continue
        try:
            revised_at = datetime.fromisoformat(str(revised_at_raw).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if revised_at.tzinfo is None:
            revised_at = revised_at.replace(tzinfo=UTC)
        if revised_at >= cutoff:
            count += 1
    return count


def _write_storm_alert(db: Session, event: AgendaEvent, *, machine_count: int, window_hours: int) -> None:
    """写 alerts 表 P1 告警（转人工复核队列）。

    alerts.rule_id 与 user_id 非空约束——风暴告警是系统级（非用户规则触发），
    为与既有 schema 兼容，rule_id 与 user_id 取系统占位（事件 owner 后续指派）。
    本实现使用 confirmed_by 兜底、否则取任一 admin 用户作为占位；若无 admin 用户，
    告警写不进 alerts 表（保留 revision_log + audit_logs 留痕，不阻塞流程）。
    """
    from sqlalchemy import select

    from app.models.alert import Alert, AlertRule
    from app.models.user import User

    # 取一个 admin 用户作为风暴告警占位（规则触发人语义）；无 admin 则跳过
    admin = db.scalars(select(User).where(User.role == "admin").limit(1)).first()
    if admin is None:
        logger.warning(
            "revision_storm_alert_skipped_no_admin",
            event_id=str(event.id),
            machine_count=machine_count,
        )
        return

    # 找/建系统级"修正风暴"规则（name 占位，用户维度）
    rule = db.scalars(
        select(AlertRule).where(
            AlertRule.user_id == admin.id,
            AlertRule.name == "__system_revision_storm__",
        ).limit(1)
    ).first()
    if rule is None:
        rule = AlertRule(
            user_id=admin.id,
            name="__system_revision_storm__",
            country_codes=[],
            topic_id=event.topic_id,
            keywords=["revision_storm"],
            condition_type="top_n",
            condition_value=0,
            active_period="all_day",
            notify_channels=["inapp"],
            enabled=True,
        )
        db.add(rule)
        db.flush()

    payload = {
        "priority": "P1",
        "kind": "revision_storm",
        "event_id": str(event.id),
        "topic_id": str(event.topic_id),
        "machine_revision_count": machine_count,
        "window_hours": window_hours,
        "triggered_at": datetime.now(UTC).isoformat(),
    }
    alert = Alert(
        rule_id=rule.id,
        user_id=admin.id,
        payload=payload,
        status="unread",
    )
    db.add(alert)
    db.flush()
    logger.info(
        "revision_storm_alert_written",
        event_id=str(event.id),
        alert_id=str(alert.id),
        machine_count=machine_count,
    )


def check_revision_storm(
    db: Session,
    event: AgendaEvent,
    *,
    settings: AgendaSettings | None = None,
    now: datetime | None = None,
) -> bool:
    """单议题窗口内机器修正 > revision_storm_threshold 时冻结自动修正并转人工复核。

    动作（幂等：重复触发只在首次锁定全部字段并写一次告警）：
    1. 统计窗口内 actor='machine' 的 revision_log 条数
    2. 若 count > threshold：event.human_locked_fields 并入 STORM_LOCKED_FIELDS
    3. 写 alerts 表 P1 告警（系统级"修正风暴"规则，首次触发时创建）
    4. 返回 True（已冻结）；否则 False

    已冻结（human_locked_fields 已包含全部 STORM_LOCKED_FIELDS）的事件再次调用
    直接返回 True，不重复写告警（幂等）。
    """
    cfg = settings or get_agenda_settings()
    window_hours = cfg.revision_storm_window_hours
    threshold = cfg.revision_storm_threshold

    # 幂等：已完全冻结的事件直接返回 True（不重复告警）
    current_locked = set(event.human_locked_fields or [])
    if set(STORM_LOCKED_FIELDS).issubset(current_locked):
        return True

    count = _count_recent_machine_revisions(event, window_hours=window_hours, now=now)
    if count <= threshold:
        return False

    # 冻结：human_locked_fields 并入 STORM_LOCKED_FIELDS（去重）
    new_locked = sorted(current_locked | set(STORM_LOCKED_FIELDS))
    event.human_locked_fields = new_locked
    db.flush()

    _write_storm_alert(db, event, machine_count=count, window_hours=window_hours)

    logger.warning(
        "revision_storm_frozen",
        event_id=str(event.id),
        topic_id=str(event.topic_id),
        machine_revision_count=count,
        threshold=threshold,
        window_hours=window_hours,
        locked_fields=new_locked,
    )
    return True


__all__ = [
    "STORM_LOCKED_FIELDS",
    "check_revision_storm",
    "maybe_deescalate",
    "maybe_escalate",
]
