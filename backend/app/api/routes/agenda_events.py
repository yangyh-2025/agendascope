"""agenda-events 模块端点（T3.15 人工确认/否决 + 详细设计 1.8/1.8.1）。

包含：
- POST /agenda-events/{event_id}/confirm：人工确认（authorized）
- POST /agenda-events/{event_id}/revisions/{seq}/reject：人工否决某条机器修正（authorized）
- GET  /agenda-events/{event_id}/revisions：拉取该事件 revision_log（registered）

审计：所有写操作写 audit_logs（action=agenda_event.confirm / agenda_event.revision_reject）。
"""
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agenda_engine.revision import (
    RevisionError,
    confirm_event,
    reject_revision,
)
from app.api.deps import ROLE_AUTHORIZED, ROLE_REGISTERED, require_role
from app.core.errors import CODE_NOT_FOUND, BizError, ok
from app.db.session import get_db
from app.models.agenda import AgendaEvent
from app.models.user import User
from app.repositories.audit_repo import write_audit

router = APIRouter()


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


__all__ = ["router"]
