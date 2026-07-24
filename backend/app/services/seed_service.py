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


# GDELT 兜底通道伪源：GDELT 文章无法归属具体已登记源时挂靠此源
GDELT_PSEUDO_SOURCE_NAME = "GDELT 兜底通道"


def ensure_gdelt_pseudo_source(db: Session):
    from app.models.source import Source

    source = db.scalar(select(Source).where(Source.name == GDELT_PSEUDO_SOURCE_NAME))
    if source is not None:
        return source
    source = Source(
        name=GDELT_PSEUDO_SOURCE_NAME,
        name_zh="GDELT 兜底通道",
        country_code="ZZ",  # 跨国聚合通道，不冒充任何单一国家
        homepage_url="https://www.gdeltproject.org",
        feed_url=None,
        collect_mode="gdelt",
        adapter_type="rss",
        media_type="online",
        language="en",
        poll_interval_min=15,
        coverage_confidence="low",
        is_custom=False,
    )
    db.add(source)
    db.flush()
    return source
