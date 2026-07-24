"""LLM 降级 P1 告警（T2.16）。

复用 collector 治理的告警模式（governance.maybe_alert_source_fail_rate）：
系统规则 + 管理员收件 + Redis 防抖，写 alerts 表，绝不静默降级。
"""
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alert import Alert, AlertRule
from app.services.seed_service import ensure_admin

logger = structlog.get_logger(__name__)

# 系统内置规则名：LLM 服务降级监控（详细设计 6.2 llm_service 降级行）
SYSTEM_LLM_HEALTH_RULE = "系统-LLM服务监控"


def ensure_llm_health_rule(db: Session) -> AlertRule:
    rule = db.scalar(select(AlertRule).where(AlertRule.name == SYSTEM_LLM_HEALTH_RULE))
    if rule is not None:
        return rule
    admin = ensure_admin(db)
    rule = AlertRule(
        user_id=admin.id,
        name=SYSTEM_LLM_HEALTH_RULE,
        country_codes=[],
        keywords=["__llm_health__"],
        condition_type="growth_rate",
        condition_value=0,
        notify_channels=["inapp"],
    )
    db.add(rule)
    db.flush()
    return rule


def write_llm_degraded_alert(
    db: Session,
    reason: str,
    since: datetime | None = None,
    redis_client: Any = None,
    debounce_seconds: int = 3600,
) -> bool:
    """写 LLM 降级 P1 告警（防抖：默认 1h 内不重复）。返回是否真正写入。"""
    if redis_client is not None:
        debounce_key = "alert:llm_degraded"
        if redis_client.exists(debounce_key):
            return False
        redis_client.setex(debounce_key, debounce_seconds, "1")

    admin = ensure_admin(db)
    rule = ensure_llm_health_rule(db)
    db.add(Alert(
        rule_id=rule.id,
        user_id=admin.id,
        payload={
            "kind": "llm_degraded",
            "severity": "P1",
            "component": "llm_service",
            "fallback": "ctfidf_fallback",
            "reason": reason,
            "since": (since or datetime.now(UTC)).isoformat(),
        },
    ))
    db.flush()
    logger.warning("llm_degraded_alert", severity="P1", fallback="ctfidf_fallback", reason=reason)
    return True
