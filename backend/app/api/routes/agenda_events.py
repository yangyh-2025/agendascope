"""agenda-events 模块端点（详细设计 1.8 + T3.15 人工确认/否决/排除）。

包含：
- GET  /agenda-events：列表（status/首发国/时间窗筛选，仅 authorized+）
- GET  /agenda-events/{id}：详情（含 follower_sequence/stats_evidence/final_review/revision_log）
- POST /agenda-events/{id}/confirm：人工确认（authorized）
- POST /agenda-events/{id}/dismiss：人工排除（authorized）
- POST /agenda-events/{id}/revisions/{seq}/reject：人工否决某条机器修正（authorized）
- GET  /agenda-events/{id}/revisions：拉取该事件 revision_log（registered）

审计：所有写操作写 audit_logs（action=agenda_event.confirm/dismiss/revision_reject/read）。
"""
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.agenda_engine.revision import (
    ACTOR_HUMAN,
    RevisionError,
    append_revision,
    confirm_event,
    reject_revision,
)
from app.api.deps import ROLE_AUTHORIZED, ROLE_REGISTERED, require_role
from app.core.errors import (
    CODE_NOT_FOUND,
    CODE_PARAM_INVALID,
    CODE_STATE_INVALID,
    BizError,
    ok,
)
from app.db.session import get_db
from app.models.agenda import AgendaEvent
from app.models.person import PersonOrg
from app.models.source import Source
from app.models.topic import Topic
from app.models.user import User
from app.repositories.audit_repo import write_audit
from app.schemas.agenda import DismissEventRequest

router = APIRouter()

_EVENT_STATUSES = {"watching", "suspected", "confirmed", "dismissed", "revised", "archived"}

# confidence 排序显式优先级映射（修复：此前按字符串字典序 asc，语义错误）
_CONFIDENCE_SORT_PRIORITY = {"confirmed": 0, "suspected": 1, "watching": 2}


class RejectRevisionRequest(BaseModel):
    """POST /agenda-events/{event_id}/revisions/{seq}/reject 请求体（详细设计 1.8.1）。"""

    reason: str = Field(min_length=1, max_length=500, description="否决原因（必填，≤500 字）")


def _get_event_or_404(db: Session, event_id: uuid.UUID) -> AgendaEvent:
    event = db.get(AgendaEvent, event_id)
    if event is None:
        raise BizError(CODE_NOT_FOUND, f"事件不存在: {event_id}")
    return event


def _serialize_revision(entry: dict) -> dict[str, Any]:
    """revision_log 单条序列化（保持字段口径与详细设计 2.10 COMMENT 一致）。"""
    return {
        "seq": entry.get("seq"),
        "revised_at": entry.get("revised_at"),
        "field": entry.get("field"),
        "before_value": entry.get("before_value"),
        "after_value": entry.get("after_value"),
        "trigger_evidence": entry.get("trigger_evidence"),
        "actor": entry.get("actor"),
        "actor_id": entry.get("actor_id"),
        "model": entry.get("model"),
        "prompt_version": entry.get("prompt_version"),
        "rejected": bool(entry.get("rejected", False)),
    }


def _serialize_event_brief(event: AgendaEvent) -> dict[str, Any]:
    """事件关键字段快照（confirm/reject 响应体复用）。"""
    return {
        "id": str(event.id),
        "topic_id": str(event.topic_id),
        "status": event.status,
        "confidence": event.confidence,
        "origin_type": event.origin_type,
        "origin_country_code": event.origin_country_code,
        "origin_at": event.origin_at.isoformat() if event.origin_at else None,
        "origin_confidence": event.origin_confidence,
        "human_locked_fields": list(event.human_locked_fields or []),
        "confirmed_by": str(event.confirmed_by) if event.confirmed_by else None,
        "confirmed_at": event.confirmed_at.isoformat() if event.confirmed_at else None,
    }


def _follower_summary(follower_sequence: list | None) -> tuple[int, float | None]:
    """从 follower_sequence 提取跟随国数与最大时滞。"""
    if not follower_sequence:
        return 0, None
    lags = [
        float(f.get("lag_hours", 0))
        for f in follower_sequence
        if isinstance(f, dict)
    ]
    return len(follower_sequence), (max(lags) if lags else None)


@router.get("")
def list_events(
    status: str | None = Query(default=None),
    origin_country_code: str | None = Query(default=None, min_length=2, max_length=2),
    topic_id: uuid.UUID | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    sort: str = Query(default="updated_at", pattern="^(updated_at|origin_at|confidence)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_AUTHORIZED)),
):
    """事件列表（详细设计 1.8）：事件属机密级，仅 authorized+ 可见。"""
    if status and status not in _EVENT_STATUSES:
        raise BizError(CODE_PARAM_INVALID, f"status 仅支持 {sorted(_EVENT_STATUSES)}")

    stmt = select(AgendaEvent)
    if status:
        stmt = stmt.where(AgendaEvent.status == status)
    if origin_country_code:
        stmt = stmt.where(AgendaEvent.origin_country_code == origin_country_code.upper())
    if topic_id:
        stmt = stmt.where(AgendaEvent.topic_id == topic_id)
    if date_from:
        if date_from.tzinfo is None:
            date_from = date_from.replace(tzinfo=UTC)
        stmt = stmt.where(AgendaEvent.origin_at >= date_from)
    if date_to:
        if date_to.tzinfo is None:
            date_to = date_to.replace(tzinfo=UTC)
        stmt = stmt.where(AgendaEvent.origin_at <= date_to)

    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)

    if sort == "origin_at":
        stmt = stmt.order_by(AgendaEvent.origin_at.desc())
    elif sort == "confidence":
        # 显式优先级映射：confirmed > suspected > watching，同级按更新时间倒序
        stmt = stmt.order_by(
            case(
                *[
                    (AgendaEvent.confidence == level, priority)
                    for level, priority in _CONFIDENCE_SORT_PRIORITY.items()
                ],
                else_=len(_CONFIDENCE_SORT_PRIORITY),
            ),
            AgendaEvent.updated_at.desc(),
        )
    else:
        stmt = stmt.order_by(AgendaEvent.updated_at.desc())

    rows = list(db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all())

    # 取关联议题名
    topic_ids = list({r.topic_id for r in rows})
    topic_names: dict[str, str] = {}
    if topic_ids:
        for t in db.scalars(select(Topic).where(Topic.id.in_(topic_ids))).all():
            topic_names[str(t.id)] = t.name_zh or t.name

    items: list[dict[str, Any]] = []
    for ev in rows:
        follower_count, max_lag = _follower_summary(ev.follower_sequence)
        final_review_score = None
        if isinstance(ev.final_review, dict):
            final_review_score = ev.final_review.get("score")
        stats_significant = False
        if isinstance(ev.stats_evidence, dict):
            for k in ("xcorr", "granger", "qap"):
                sub = ev.stats_evidence.get(k)
                if isinstance(sub, dict) and sub.get("significant"):
                    stats_significant = True
                    break
        items.append({
            "id": str(ev.id),
            "topic_id": str(ev.topic_id),
            "topic_name": topic_names.get(str(ev.topic_id)),
            "status": ev.status,
            "confidence": ev.confidence,
            "origin_type": ev.origin_type,
            "origin_country_code": ev.origin_country_code,
            "origin_at": ev.origin_at.isoformat() if ev.origin_at else None,
            "origin_confidence": ev.origin_confidence,
            "follower_count": follower_count,
            "max_lag_hours": max_lag,
            "stats_significant": stats_significant,
            "detection_method": ev.detection_method,
            "final_review_score": final_review_score,
            "confirmed_by": str(ev.confirmed_by) if ev.confirmed_by else None,
            "confirmed_at": ev.confirmed_at.isoformat() if ev.confirmed_at else None,
            "updated_at": ev.updated_at.isoformat() if ev.updated_at else None,
            "created_at": ev.created_at.isoformat() if ev.created_at else None,
        })

    return ok({
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    })


@router.get("/{event_id}")
def event_detail(
    event_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_AUTHORIZED)),
):
    """事件详情（详细设计 1.8 + 7.2 审计：机密对象查看留痕 agenda_event.read）。"""
    event = _get_event_or_404(db, event_id)
    data = _serialize_event_brief(event)
    data.update({
        "origin_source_id": str(event.origin_source_id) if event.origin_source_id else None,
        "origin_entity_id": str(event.origin_entity_id) if event.origin_entity_id else None,
        "origin_quote": event.origin_quote,
        "follower_sequence": list(event.follower_sequence or []),
        "stats_evidence": event.stats_evidence,
        "final_review": event.final_review,
        "detection_method": event.detection_method,
        "round_no": event.round_no,
        "revision_log": [_serialize_revision(e) for e in (event.revision_log or []) if isinstance(e, dict)],
        "dismiss_reason": event.dismiss_reason,
        "is_false_positive": event.is_false_positive,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "updated_at": event.updated_at.isoformat() if event.updated_at else None,
    })
    # 议题名
    topic = db.get(Topic, event.topic_id)
    data["topic_name"] = (topic.name_zh or topic.name) if topic else None
    # 首发媒体/实体简要信息
    if event.origin_source_id:
        src = db.get(Source, event.origin_source_id)
        data["origin_source"] = (
            {"id": str(src.id), "name": src.name, "country_code": src.country_code}
            if src else None
        )
    else:
        data["origin_source"] = None
    if event.origin_entity_id:
        ent = db.get(PersonOrg, event.origin_entity_id)
        data["origin_entity"] = (
            {
                "id": str(ent.id),
                "name": ent.name,
                "entity_type": ent.entity_type,
                "country_code": ent.country_code,
            }
            if ent else None
        )
    else:
        data["origin_entity"] = None

    # 审计：机密事件查看计入 audit_logs（详细设计 1.8 详情查看条目）
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent", "")
    write_audit(
        db, "agenda_event.read", user=user,
        resource=f"agenda-events/{event_id}",
        detail={"status": event.status, "confidence": event.confidence},
        ip=ip, user_agent=ua,
    )
    db.commit()
    return ok(data)


@router.post("/{event_id}/confirm")
def confirm_event_endpoint(
    event_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_AUTHORIZED)),
):
    """人工确认（详细设计 1.8）：status/confidence → 'confirmed'。

    留痕：revision_log 追加 actor='human' field='status' 条目 + audit_logs。
    422 (4002)：已 confirmed/dismissed/archived 不可重复确认。
    """
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent", "")
    try:
        event = confirm_event(db, event_id, actor_user_id=user.id)
    except RevisionError as exc:
        write_audit(
            db, "agenda_event.confirm", user=user,
            resource=f"agenda-events/{event_id}",
            detail={"error": exc.message},
            ip=ip, user_agent=ua, result="failure",
        )
        db.commit()
        raise

    write_audit(
        db, "agenda_event.confirm", user=user,
        resource=f"agenda-events/{event_id}",
        detail={"status": event.status, "confidence": event.confidence},
        ip=ip, user_agent=ua,
    )
    db.commit()
    return ok(_serialize_event_brief(event))


@router.post("/{event_id}/dismiss")
def dismiss_event_endpoint(
    event_id: uuid.UUID,
    body: DismissEventRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_AUTHORIZED)),
):
    """人工排除事件（详细设计 1.8 + PRD 8.4 误报反馈闭环）。

    status: watching/suspected/revised → dismissed；
    写 revision_log(actor='human', field='status', trigger_evidence={'type':'manual_dismiss'})
    + audit_logs(action=agenda_event.dismiss)。
    422 (4002)：已 confirmed/dismissed/archived 不可排除。
    """
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent", "")
    event = _get_event_or_404(db, event_id)
    if event.status in ("confirmed", "dismissed", "archived"):
        write_audit(
            db, "agenda_event.dismiss", user=user,
            resource=f"agenda-events/{event_id}",
            detail={"error": f"当前状态 {event.status} 不可排除"},
            ip=ip, user_agent=ua, result="failure",
        )
        db.commit()
        raise BizError(
            CODE_STATE_INVALID,
            f"事件已 {event.status}，不可排除",
        )

    old_status = event.status
    append_revision(
        db, event,
        field="status",
        before_value=old_status,
        after_value="dismissed",
        trigger_evidence={
            "type": "manual_dismiss",
            "reason": body.reason,
            "false_positive": body.false_positive,
        },
        actor=ACTOR_HUMAN,
        actor_id=user.id,
    )
    event.status = "dismissed"
    event.dismiss_reason = body.reason
    event.is_false_positive = bool(body.false_positive)
    db.flush()

    write_audit(
        db, "agenda_event.dismiss", user=user,
        resource=f"agenda-events/{event_id}",
        detail={
            "previous_status": old_status,
            "reason": body.reason,
            "false_positive": body.false_positive,
        },
        ip=ip, user_agent=ua,
    )
    db.commit()
    return ok(_serialize_event_brief(event))


@router.post("/{event_id}/revisions/{seq}/reject")
def reject_revision_endpoint(
    event_id: uuid.UUID,
    seq: int,
    body: RejectRevisionRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_AUTHORIZED)),
):
    """人工否决某条机器修正（详细设计 1.8.1）：
    回滚 event.<field> 到 before_value + 追加人工 revision + human_locked_fields 增加。

    404 (3001)：事件或修正记录不存在。
    422 (4002)：修正已被否决 / 修正本身为人工操作 / 事件已 archived。
    """
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent", "")
    try:
        event = reject_revision(
            db, event_id, seq, actor_user_id=user.id, reason=body.reason
        )
    except RevisionError as exc:
        write_audit(
            db, "agenda_event.revision_reject", user=user,
            resource=f"agenda-events/{event_id}/revisions/{seq}",
            detail={"error": exc.message, "reason": body.reason},
            ip=ip, user_agent=ua, result="failure",
        )
        db.commit()
        raise

    # 取最新一条 revision（人工追加的回滚条目）作为响应
    latest = (event.revision_log or [])[-1] if event.revision_log else {}
    write_audit(
        db, "agenda_event.revision_reject", user=user,
        resource=f"agenda-events/{event_id}/revisions/{seq}",
        detail={
            "field": latest.get("field"),
            "before_value": latest.get("before_value"),
            "after_value": latest.get("after_value"),
            "reason": body.reason,
        },
        ip=ip, user_agent=ua,
    )
    db.commit()

    data = _serialize_event_brief(event)
    data["revision_appended"] = _serialize_revision(latest) if latest else None
    return ok(data)


@router.get("/{event_id}/revisions")
def list_revisions_endpoint(
    event_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    """拉取该事件的 revision_log（含 rejected 标记，registered 即可读）。"""
    event = _get_event_or_404(db, event_id)
    revisions = [_serialize_revision(e) for e in (event.revision_log or []) if isinstance(e, dict)]
    return ok({
        "event_id": str(event.id),
        "topic_id": str(event.topic_id),
        "status": event.status,
        "confidence": event.confidence,
        "human_locked_fields": list(event.human_locked_fields or []),
        "revisions": revisions,
    })


@router.get("/{event_id}/chain")
def event_chain(
    event_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_AUTHORIZED)),
):
    """传播链路（详细设计 1.8 链路图数据）：首发国 → 跟随国时滞序列 + 边集合。

    数据全部来自引擎落库字段：origin_* / follower_sequence（JSONB，
    元素含 country_code/first_media_name/first_published_at/lag_hours）。
    """
    event = _get_event_or_404(db, event_id)

    origin_media = None
    if event.origin_source_id:
        src = db.get(Source, event.origin_source_id)
        if src is not None:
            origin_media = {"id": str(src.id), "name": src.name}

    followers: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for f in event.follower_sequence or []:
        if not isinstance(f, dict):
            continue
        followers.append({
            "country": f.get("country_code"),
            "first_media": f.get("first_media_name"),
            "first_article_id": f.get("first_article_id"),
            "first_published_at": f.get("first_published_at"),
            "lag_hours": float(f.get("lag_hours", 0)),
        })
        edges.append({
            "from_country": event.origin_country_code,
            "to_country": f.get("country_code"),
            "lag_hours": float(f.get("lag_hours", 0)),
        })

    return ok({
        "event_id": str(event.id),
        "topic_id": str(event.topic_id),
        "origin": {
            "country": event.origin_country_code,
            "media": origin_media,
            "published_at": event.origin_at.isoformat() if event.origin_at else None,
            "confidence": event.origin_confidence,
        },
        "follower_sequence": followers,
        "edges": edges,
    })


__all__ = ["router"]
