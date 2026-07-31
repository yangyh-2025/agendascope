"""report-exports API（T4.17，详细设计 1.11）：创建 / 历史 / 状态 / 下载。

契约（双兼容）：
  POST {template|report_type, format: pdf|docx, scope|params{topic_id?,countries?,from,to},
        time_range{from,to}?, locale?}
  90 天时间窗预检（超限 code=1001）；并发 >3 排队（status=pending）；
  60s 内生成完成返回 done + download_url，否则返回 processing + queue_position，完成后站内通知。
"""
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import ROLE_REGISTERED, get_db, require_license_active, require_role
from app.core.errors import CODE_FORBIDDEN, CODE_NOT_FOUND, CODE_STATE_INVALID, BizError, ok
from app.db.session import get_session_factory
from app.models.report import ReportExport
from app.models.topic import Topic
from app.models.user import User
from app.repositories.audit_repo import write_audit
from app.services import report_service

router = APIRouter()

_MEDIA_TYPES = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


class CreateExportRequest(BaseModel):
    """POST /report-exports 请求体：template/report_type 双兼容，scope/params/time_range 归一化在服务层完成。"""

    template: str | None = None
    report_type: str | None = None
    format: str | None = None
    scope: dict | None = None
    params: dict | None = None
    time_range: dict | None = None
    locale: str | None = None

    def to_payload(self) -> dict:
        return self.model_dump(exclude_none=True)


def _get_export_or_404(db: Session, export_id: uuid.UUID, user: User) -> ReportExport:
    export = db.get(ReportExport, export_id)
    if export is None:
        raise BizError(CODE_NOT_FOUND, f"导出任务不存在: {export_id}")
    if export.user_id != user.id and user.role != "admin":
        raise BizError(CODE_FORBIDDEN, "仅可访问自己的导出任务")
    return export


def _scope_summary(db: Session, export: ReportExport) -> str:
    scope = dict(export.scope or {})
    topic_name = None
    if scope.get("topic_id"):
        topic = db.get(Topic, uuid.UUID(scope["topic_id"]))
        topic_name = (topic.name_zh or topic.name) if topic else None
    return report_service.scope_summary(export.template, scope, topic_name)


def _serialize(db: Session, export: ReportExport) -> dict:
    return {
        "id": str(export.id),
        "template": export.template,
        "report_type": export.template,  # 双契约字段别名
        "format": export.format,
        "status": export.status,
        "scope_summary": _scope_summary(db, export),
        "file_size": export.file_size,
        "duration_ms": export.duration_ms,
        "error": export.error,
        "created_at": export.created_at.isoformat() if export.created_at else None,
        "expires_at": export.expires_at.isoformat() if export.expires_at else None,
    }


@router.post("")
def create_export(
    body: CreateExportRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
    _license: None = Depends(require_license_active),
):
    """创建导出任务：90 天预检 → 并发 >3 排队 → 60s 内联尝试，超时转异步。"""
    export = report_service.create_export(db, user.id, body.to_payload())
    active = report_service.count_active_exports(db)
    db.flush()

    ip = request.client.host if request.client else None
    write_audit(
        db, "report.export", user=user,
        resource=f"report-exports/{export.id}",
        detail={"template": export.template, "format": export.format, "scope": export.scope},
        ip=ip, user_agent=request.headers.get("user-agent", ""),
    )
    db.commit()

    # 并发 >3：直接排队，等 worker 执行
    if active > report_service.MAX_CONCURRENT_EXPORTS:
        return ok(
            {"id": str(export.id), "status": "pending", "queue_position": active - report_service.MAX_CONCURRENT_EXPORTS},
            message="导出排队中，完成后站内通知",
        )

    result = report_service.run_inline_with_timeout(
        export.id, get_session_factory(), report_service.DEFAULT_EXPORT_DIR,
    )
    db.expire_all()
    fresh = db.get(ReportExport, export.id)
    assert fresh is not None, "export must exist after creation"
    if result.async_mode or fresh.status in ("pending", "processing"):
        return ok(
            {"id": str(fresh.id), "status": fresh.status, "queue_position": 0},
            message="报告生成中，完成后站内通知",
        )
    if fresh.status == "failed":
        return ok(
            {"id": str(fresh.id), "status": "failed", "error": fresh.error},
            message="报告生成失败",
        )
    return ok({
        "id": str(fresh.id),
        "status": "done",
        "download_url": f"/api/v1/report-exports/{fresh.id}/download",
        "duration_ms": fresh.duration_ms,
        "file_size": fresh.file_size,
    })


@router.get("")
def list_exports(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    """导出历史（仅本人；admin 全量）。"""
    stmt = select(ReportExport)
    if user.role != "admin":
        stmt = stmt.where(ReportExport.user_id == user.id)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = list(db.scalars(
        stmt.order_by(ReportExport.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all())
    return ok({
        "total": total, "page": page, "page_size": page_size,
        "items": [_serialize(db, e) for e in rows],
    })


@router.get("/{export_id}")
def export_status(
    export_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    export = _get_export_or_404(db, export_id, user)
    data = _serialize(db, export)
    data["watermark"] = export.watermark
    if export.status == "done":
        data["download_url"] = f"/api/v1/report-exports/{export.id}/download"
    return ok(data)


@router.get("/{export_id}/download")
def download_export(
    export_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_REGISTERED)),
):
    export = _get_export_or_404(db, export_id, user)
    if export.status != "done":
        raise BizError(CODE_STATE_INVALID, f"任务未完成（status={export.status}），暂不可下载")
    if export.expires_at and export.expires_at.replace(tzinfo=UTC) < datetime.now(UTC):
        raise BizError(CODE_NOT_FOUND, "文件已过期（保留 7 天），请重新导出")
    if not export.file_path or not Path(export.file_path).is_file():
        raise BizError(CODE_NOT_FOUND, "文件已过期或已被清理，请重新导出")

    ip = request.client.host if request.client else None
    write_audit(
        db, "report.download", user=user,
        resource=f"report-exports/{export.id}",
        detail={"template": export.template, "file_size": export.file_size},
        ip=ip, user_agent=request.headers.get("user-agent", ""),
    )
    db.commit()

    filename = f"AgendaScope_{export.template}_{export.id}.{export.format}"
    return FileResponse(
        export.file_path,
        media_type=_MEDIA_TYPES.get(export.format, "application/octet-stream"),
        filename=filename,
    )


__all__ = ["router"]
