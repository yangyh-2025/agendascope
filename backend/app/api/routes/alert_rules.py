"""alert_rules CRUD API（T4.14）。"""
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func as sql_func, select
from sqlalchemy.orm import Session

from app.api.deps import ROLE_ADMIN, ROLE_AUTHORIZED, get_db, require_license_active, require_role
from app.core.errors import CODE_FORBIDDEN, CODE_NOT_FOUND, CODE_QUOTA_EXCEEDED, BizError, ok
from app.models.alert import AlertRule
from app.models.user import User

router = APIRouter()

MAX_RULES_REGISTERED = 5
MAX_RULES_AUTHORIZED = 50


class CreateRuleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    country_codes: list[str] = Field(max_length=10)
    topic_id: uuid.UUID | None = None
    keywords: list[str] | None = Field(None, max_length=10)
    condition_type: str = Field(pattern=r"^(growth_rate|top_n|neg_ratio)$")
    condition_value: float
    condition_extra: list | None = None
    notify_channels: list[str] = Field(default=["inapp", "email"])
    webhook_url: str | None = None


class UpdateRuleRequest(BaseModel):
    name: str | None = None
    keywords: list[str] | None = None
    condition_value: float | None = None
    notify_channels: list[str] | None = None
    webhook_url: str | None = None
    enabled: bool | None = None


def _check_quota(db: Session, user: User) -> None:
    limit = MAX_RULES_AUTHORIZED if user.role in (ROLE_AUTHORIZED, ROLE_ADMIN) else MAX_RULES_REGISTERED
    count = db.scalar(select(sql_func.count()).where(AlertRule.user_id == user.id)) or 0
    if count >= limit:
        raise BizError(CODE_QUOTA_EXCEEDED, f"预警规则已达上限（{limit} 条），请删除后新建")


@router.get("")
def list_rules(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_AUTHORIZED)),
):
    from sqlalchemy import func as sql_func
    stmt = select(AlertRule)
    if user.role != ROLE_ADMIN:
        stmt = stmt.where(AlertRule.user_id == user.id)
    total = db.scalar(select(sql_func.count()).select_from(stmt.subquery())) or 0
    offset = (page - 1) * page_size
    rules = db.scalars(stmt.order_by(AlertRule.created_at.desc()).offset(offset).limit(page_size)).all()
    items = [{
        "id": str(r.id), "user_id": str(r.user_id), "name": r.name,
        "country_codes": r.country_codes, "topic_id": str(r.topic_id) if r.topic_id else None,
        "keywords": r.keywords, "condition_type": r.condition_type,
        "condition_value": float(r.condition_value) if r.condition_value else 0,
        "notify_channels": r.notify_channels, "webhook_url": r.webhook_url,
        "enabled": r.enabled, "last_triggered_at": r.last_triggered_at.isoformat() if r.last_triggered_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rules]
    return ok({"total": total, "page": page, "page_size": page_size, "items": items})


@router.post("")
def create_rule(body: CreateRuleRequest, db: Session = Depends(get_db), user: User = Depends(require_role(ROLE_AUTHORIZED)), _license: None = Depends(require_license_active)):
    _check_quota(db, user)
    rule = AlertRule(
        user_id=user.id, name=body.name, country_codes=body.country_codes,
        topic_id=body.topic_id, keywords=body.keywords, condition_type=body.condition_type,
        condition_value=body.condition_value,
        condition_extra={"and": body.condition_extra} if body.condition_extra else None,
        notify_channels=body.notify_channels, webhook_url=body.webhook_url,
    )
    db.add(rule)
    db.flush()
    return ok({"id": str(rule.id)})


@router.patch("/{rule_id}")
def update_rule(rule_id: uuid.UUID, body: UpdateRuleRequest, db: Session = Depends(get_db), user: User = Depends(require_role(ROLE_AUTHORIZED)), _license: None = Depends(require_license_active)):
    rule = db.get(AlertRule, rule_id)
    if rule is None:
        raise BizError(CODE_NOT_FOUND, f"规则不存在: {rule_id}")
    if rule.user_id != user.id and user.role != ROLE_ADMIN:
        raise BizError(CODE_FORBIDDEN, "仅可编辑自己的规则")
    if body.name is not None:
        rule.name = body.name
    if body.keywords is not None:
        rule.keywords = body.keywords
    if body.condition_value is not None:
        rule.condition_value = body.condition_value
    if body.notify_channels is not None:
        rule.notify_channels = body.notify_channels
    if body.webhook_url is not None:
        rule.webhook_url = body.webhook_url
    if body.enabled is not None:
        rule.enabled = body.enabled
    db.flush()
    return ok({"id": str(rule.id)})


@router.delete("/{rule_id}")
def delete_rule(rule_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_role(ROLE_AUTHORIZED)), _license: None = Depends(require_license_active)):
    rule = db.get(AlertRule, rule_id)
    if rule is None:
        raise BizError(CODE_NOT_FOUND, f"规则不存在: {rule_id}")
    if rule.user_id != user.id and user.role != ROLE_ADMIN:
        raise BizError(CODE_FORBIDDEN, "仅可删除自己的规则")
    db.delete(rule)
    db.flush()
    return ok(None)


__all__ = ["router"]
