"""audit_logs 写入（只增不改）。"""
from sqlalchemy.orm import Session

from app.models.audit import AuditLog


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
        ip=ip,
        user_agent=user_agent,
        result=result,
    )
    db.add(entry)
    db.flush()
