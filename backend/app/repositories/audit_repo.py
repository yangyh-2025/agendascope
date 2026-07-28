"""audit_logs 写入（只增不改）与查询（T1.10 本地留存可导出）。"""
import ipaddress
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog


def _valid_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    try:
        ipaddress.ip_address(ip)
        return ip
    except ValueError:
        return None


def write_audit(
    db: Session,
    action: str,
    user=None,
    resource: str | None = None,
    detail: dict | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    result: str = "success",
) -> None:
    entry = AuditLog(
        user_id=getattr(user, "id", None),
        username=getattr(user, "username", None),
        action=action,
        resource=resource,
        detail=detail,
        ip=_valid_ip(ip),
        user_agent=user_agent,
        result=result,
    )
    db.add(entry)
    db.flush()


def query_audit_logs(
    db: Session,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    actor: str | None = None,
    action: str | None = None,
    result: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[AuditLog], int]:
    """审计日志查询：按时间窗/操作人/动作/结果过滤 + 分页（按时间倒序）。返回 (items, total)。"""
    stmt = select(AuditLog)
    if start is not None:
        stmt = stmt.where(AuditLog.at >= start)
    if end is not None:
        stmt = stmt.where(AuditLog.at <= end)
    if actor:
        stmt = stmt.where(AuditLog.username == actor)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if result:
        stmt = stmt.where(AuditLog.result == result)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(AuditLog.at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return list(items), total
