"""审计日志查询与导出 API（T1.10：本地留存可导出，管理员限定）。

集成接线（router.py 注册，本阶段不改动聚合文件）：
    api_router.include_router(audit.router, prefix="/system/audit-logs", tags=["system"])
"""
import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import ROLE_ADMIN, require_role
from app.core.errors import ok
from app.db.session import get_db
from app.models.audit import AuditLog
from app.models.user import User
from app.repositories.audit_repo import query_audit_logs

router = APIRouter()

_EXPORT_MAX_ROWS = 10000


def _serialize(entry: AuditLog) -> dict:
    return {
        "id": str(entry.id),
        "at": entry.at.isoformat() if entry.at else None,
        "username": entry.username,
        "action": entry.action,
        "resource": entry.resource,
        "detail": entry.detail,
        "ip": entry.ip,
        "user_agent": entry.user_agent,
        "result": entry.result,
    }


class _AuditFilters:
    """列表与导出共用的过滤参数。"""

    def __init__(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        actor: str | None = None,
        action: str | None = None,
        result: str | None = None,
    ):
        self.start = start
        self.end = end
        self.actor = actor
        self.action = action
        self.result = result


def _audit_filters(
    start: datetime | None = Query(default=None, description="起始时间（ISO8601，含）"),
    end: datetime | None = Query(default=None, description="截止时间（ISO8601，含）"),
    actor: str | None = Query(default=None, max_length=64, description="操作人用户名"),
    action: str | None = Query(default=None, max_length=50, description="动作，如 auth.login"),
    result: str | None = Query(default=None, pattern="^(success|failure|denied)$", description="结果"),
) -> _AuditFilters:
    return _AuditFilters(start=start, end=end, actor=actor, action=action, result=result)


@router.get("")
def list_audit_logs(
    filters: _AuditFilters = Depends(_audit_filters),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_ADMIN)),
):
    """审计日志列表：按时间/actor/action/result 过滤 + 分页（时间倒序）。"""
    items, total = query_audit_logs(
        db,
        start=filters.start,
        end=filters.end,
        actor=filters.actor,
        action=filters.action,
        result=filters.result,
        page=page,
        page_size=page_size,
    )
    return ok({
        "items": [_serialize(e) for e in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    })


def build_audit_csv(entries: list[AuditLog]) -> str:
    """审计日志 CSV（utf-8-sig 便于 Excel 打开）。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["at", "username", "action", "resource", "result", "ip", "user_agent", "detail"])
    for e in entries:
        writer.writerow([
            e.at.isoformat() if e.at else "",
            e.username or "",
            e.action,
            e.resource or "",
            e.result,
            e.ip or "",
            e.user_agent or "",
            json.dumps(e.detail, ensure_ascii=False) if e.detail else "",
        ])
    return "﻿" + buf.getvalue()


@router.get("/export")
def export_audit_logs(
    filters: _AuditFilters = Depends(_audit_filters),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_ADMIN)),
):
    """审计日志导出：同过滤条件，CSV 下载（上限 10000 行）。"""
    items, _ = query_audit_logs(
        db,
        start=filters.start,
        end=filters.end,
        actor=filters.actor,
        action=filters.action,
        result=filters.result,
        page=1,
        page_size=_EXPORT_MAX_ROWS,
    )
    csv_text = build_audit_csv(items)
    return StreamingResponse(
        iter([csv_text]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
    )


__all__ = ["router"]
