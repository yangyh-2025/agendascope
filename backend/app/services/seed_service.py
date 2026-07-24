"""种子数据：初始管理员与系统内置预警规则（源健康监控）。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.security import hash_password
from app.models.alert import AlertRule
from app.models.user import User

# 系统内置规则名：源失败率超阈值主动告警（US-03，写入 alerts 表）
SYSTEM_SOURCE_HEALTH_RULE = "系统-源健康监控"


def ensure_admin(db: Session) -> User:
    settings = get_settings()
    user = db.scalar(select(User).where(User.username == settings.seed_admin_username))
    if user is not None:
        return user
    user = User(
        username=settings.seed_admin_username,
        password_hash=hash_password(settings.seed_admin_password),
        display_name="系统管理员",
        role="admin",
        must_change_password=True,
    )
    db.add(user)
    db.flush()
    return user


def ensure_system_rules(db: Session, admin: User) -> AlertRule:
    rule = db.scalar(select(AlertRule).where(AlertRule.name == SYSTEM_SOURCE_HEALTH_RULE))
    if rule is not None:
        return rule
    rule = AlertRule(
        user_id=admin.id,
        name=SYSTEM_SOURCE_HEALTH_RULE,
        country_codes=[],
        keywords=["__source_health__"],
        condition_type="growth_rate",
        condition_value=0,
        notify_channels=["inapp"],
    )
    db.add(rule)
    db.flush()
    return rule


def get_system_source_health_rule(db: Session) -> AlertRule | None:
    return db.scalar(select(AlertRule).where(AlertRule.name == SYSTEM_SOURCE_HEALTH_RULE))
