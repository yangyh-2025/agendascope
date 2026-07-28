"""subscriptions API（T4.16）：订阅管理 + 一键退订（免登录 token 链接）。"""
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import ROLE_REGISTERED, get_db, require_role
from app.core.errors import CODE_FORBIDDEN, CODE_NOT_FOUND, BizError, ok
from app.models.subscription import Subscription
from app.models.user import User

router = APIRouter()

MAX_SUBSCRIPTIONS_PER_USER = 10


class CreateSubscriptionRequest(BaseModel):
    country_codes: list[str] = Field(min_length=1, max_length=10)
    topic_category: str | None = Field(None, max_length=50)
    frequency: str = Field(pattern=r"^(daily|weekly)$")
    locale: str = Field(default="zh-CN", max_length=10)


def _serialize(sub: Subscription) -> dict:
    return {
        "id": str(sub.id),
        "country_codes": list(sub.country_codes or []),
        "topic_category": sub.topic_category,
        "frequency": sub.frequency,
        "locale": sub.locale,
        "enabled": sub.enabled,
        "last_sent_at": sub.last_sent_at.isoformat() if sub.last_sent_at else None,
        "created_at": sub.created_at.isoformat() if sub.created_at else None,
    }


@router.get("")
def list_subscriptions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    stmt = select(Subscription).where(Subscription.user_id == user.id)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = list(db.scalars(
        stmt.order_by(Subscription.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all())
    return ok({
        "total": total, "page": page, "page_size": page_size,
        "items": [_serialize(s) for s in rows],
    })


@router.post("")
def create_subscription(
    body: CreateSubscriptionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    count = int(db.scalar(
        select(func.count()).select_from(Subscription).where(Subscription.user_id == user.id)
    ) or 0)
    if count >= MAX_SUBSCRIPTIONS_PER_USER:
        from app.core.errors import CODE_QUOTA_EXCEEDED
        raise BizError(CODE_QUOTA_EXCEEDED, f"订阅已达上限（{MAX_SUBSCRIPTIONS_PER_USER} 条）")
    sub = Subscription(
        user_id=user.id,
        country_codes=[c.upper() for c in body.country_codes],
        topic_category=body.topic_category,
        frequency=body.frequency,
        locale=body.locale,
    )
    db.add(sub)
    db.flush()
    return ok({"id": str(sub.id), "enabled": sub.enabled})


@router.delete("/{subscription_id}")
def delete_subscription(
    subscription_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    sub = db.get(Subscription, subscription_id)
    if sub is None:
        raise BizError(CODE_NOT_FOUND, f"订阅不存在: {subscription_id}")
    if sub.user_id != user.id and user.role != "admin":
        raise BizError(CODE_FORBIDDEN, "仅可删除自己的订阅")
    db.delete(sub)
    db.flush()
    return ok(None)


@router.get("/unsubscribe")
def unsubscribe(token: str = Query(min_length=16), db: Session = Depends(get_db)):
    """一键退订（免登录）：邮件中带 token 的退订链接，点击即停用该订阅。"""
    sub = db.scalar(select(Subscription).where(Subscription.unsubscribe_token == token))
    if sub is None:
        raise BizError(CODE_NOT_FOUND, "退订链接无效或已删除")
    if sub.enabled:
        sub.enabled = False
        sub.updated_at = datetime.now(UTC)
        db.flush()
    return ok({"id": str(sub.id), "enabled": False}, message="已退订，不会再收到该推送")


__all__ = ["router"]
