"""alerts 站内信 API（详细设计 1.10）：预警记录列表 / 标记已读 / 全部已读。"""
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.orm import Session

from app.api.deps import ROLE_AUTHORIZED, get_db, require_role
from app.core.errors import CODE_FORBIDDEN, CODE_NOT_FOUND, CODE_PARAM_INVALID, BizError, ok
from app.models.alert import Alert, AlertRule
from app.models.user import User

router = APIRouter()

_ALERT_STATUSES = {"unread", "read", "archived", "suppressed"}


class ReadAllRequest(BaseModel):
    before: datetime | None = None  # 可选，仅标记该时间之前触发的预警


def _serialize_alert(alert: Alert, rule_names: dict[str, str]) -> dict:
    return {
        "id": str(alert.id),
        "rule_id": str(alert.rule_id),
        "rule_name": rule_names.get(str(alert.rule_id)),
        "triggered_at": alert.triggered_at.isoformat() if alert.triggered_at else None,
        "status": alert.status,
        "suppressed_count": int(alert.suppressed_count or 0),
        "payload": alert.payload,
        "notify_result": alert.notify_result,
        "read_at": alert.read_at.isoformat() if alert.read_at else None,
    }


@router.get("")
def list_alerts(
    status: str | None = Query(None, description="unread/read/archived/suppressed；缺省全部"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_AUTHORIZED)),
):
    """当前用户预警列表：已读/未读过滤 + 分页；附带未读总数（前端角标）。"""
    if status is not None and status not in _ALERT_STATUSES:
        raise BizError(CODE_PARAM_INVALID, f"status 仅支持 {sorted(_ALERT_STATUSES)}")

    stmt = select(Alert).where(Alert.user_id == user.id)
    if status:
        stmt = stmt.where(Alert.status == status)

    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    unread = int(db.scalar(
        select(func.count()).select_from(Alert).where(
            Alert.user_id == user.id, Alert.status == "unread",
        )
    ) or 0)

    rows = list(db.scalars(
        stmt.order_by(Alert.triggered_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all())

    rule_ids = list({a.rule_id for a in rows})
    rule_names: dict[str, str] = {}
    if rule_ids:
        for r in db.scalars(select(AlertRule).where(AlertRule.id.in_(rule_ids))).all():
            rule_names[str(r.id)] = r.name

    return ok({
        "total": total,
        "unread": unread,
        "page": page,
        "page_size": page_size,
        "items": [_serialize_alert(a, rule_names) for a in rows],
    })


@router.post("/read-all")
def read_all_alerts(
    body: ReadAllRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_AUTHORIZED)),
):
    """全部已读（详细设计 1.10）：可选 before 仅标记该时间之前。"""
    stmt = (
        update(Alert)
        .where(Alert.user_id == user.id, Alert.status == "unread")
        .values(status="read", read_at=datetime.now(UTC))
    )
    if body is not None and body.before is not None:
        before = body.before
        if before.tzinfo is None:
            before = before.replace(tzinfo=UTC)
        stmt = stmt.where(Alert.triggered_at <= before)
    result = db.execute(stmt)
    db.flush()
    marked = result.rowcount if isinstance(result, CursorResult) else 0
    return ok({"marked": int(marked or 0)})


@router.post("/{alert_id}/read")
def read_alert(
    alert_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_AUTHORIZED)),
):
    """标记单条已读（仅本人；管理员可读全部）。"""
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise BizError(CODE_NOT_FOUND, f"预警不存在: {alert_id}")
    if alert.user_id != user.id and user.role != "admin":
        raise BizError(CODE_FORBIDDEN, "仅可操作自己的预警")
    if alert.status == "unread":
        alert.status = "read"
        alert.read_at = datetime.now(UTC)
        db.flush()
    return ok({"id": str(alert.id), "status": alert.status})


__all__ = ["router"]
