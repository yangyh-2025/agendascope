"""系统管理后台 API（T5.10）：系统概览/用户管理/日志查看/许可管理/一键诊断包（T5.13）。"""
import os
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import ROLE_ADMIN, get_db, require_role
from app.config import get_settings
from app.core.errors import CODE_DATA_INSUFFICIENT, CODE_NOT_FOUND, BizError, ok
from app.core.logging import get_logger
from app.db.redis_client import get_cache_redis
from app.models.article import Article
from app.models.topic import Topic
from app.models.user import User
from app.services import diagnostics_service, license_service
from app.services.log_service import read_log_tail

router = APIRouter()
logger = get_logger("system_admin")


@router.get("/overview")
def system_overview(_user: User = Depends(require_role(ROLE_ADMIN)), db=Depends(get_db), redis_client=Depends(get_cache_redis)):
    """系统概览：CPU/内存/磁盘、当日采集量、队列积压、延迟 P95（近 24h）。

    psutil 缺失时 cpu/memory 返回 null 并标记 metrics_status=unavailable，不填 0 假数据。
    """
    import shutil
    mem_info = None
    cpu_info = None
    metrics_status = "unavailable"
    try:
        import psutil
        mem_info = {"total_mb": int(psutil.virtual_memory().total / 1024**2), "available_mb": int(psutil.virtual_memory().available / 1024**2), "percent": psutil.virtual_memory().percent}
        cpu_info = {"cores": psutil.cpu_count(), "percent": psutil.cpu_percent(interval=0.1)}
        metrics_status = "ok"
    except ImportError:
        logger.warning("overview_metrics_unavailable", reason="psutil 未安装")

    disk_gb = None
    try:
        disk_gb = round(shutil.disk_usage("/").free / 1024**3, 1)
    except OSError as exc:
        logger.warning("overview_disk_error", error=str(exc))

    from datetime import timedelta
    now = datetime.now(UTC)
    today = now.replace(hour=0, minute=0, second=0)
    article_count_today = db.scalar(select(func.count()).select_from(Article).where(Article.published_at >= today)) or 0
    topic_count = db.scalar(select(func.count()).select_from(Topic).where(Topic.lifecycle_state != "archived")) or 0
    user_count = db.scalar(select(func.count()).select_from(User)) or 0

    queue_backlog = None
    try:
        queue_backlog = redis_client.xlen("raw:articles") or 0
    except Exception as exc:  # noqa: BLE001 —— Redis 降级时指标置 null，不阻塞概览
        logger.warning("overview_queue_error", error=str(exc))

    # 延迟 P95（近 24h）：全通道 + 分通道，无样本为 null
    from app.nlp.latency import channel_stats, overall_p95_min

    since = now - timedelta(hours=24)
    latency_p95_min = overall_p95_min(db, since)
    latency_by_channel = channel_stats(db, since)

    return ok({
        "cpu": cpu_info, "memory": mem_info, "metrics_status": metrics_status,
        "disk_free_gb": disk_gb,
        "articles_today": article_count_today, "active_topics": topic_count,
        "users": user_count, "queue_backlog_raw_articles": queue_backlog,
        "latency_p95_min_24h": latency_p95_min,
        "latency_by_channel_24h": latency_by_channel,
    })


@router.get("/users")
def list_users(db=Depends(get_db), user: User = Depends(require_role(ROLE_ADMIN))):
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    items = [{
        "id": str(u.id), "username": u.username, "display_name": u.display_name,
        "role": u.role, "status": u.status, "created_at": u.created_at.isoformat() if u.created_at else None,
    } for u in users]
    return ok({"items": items})


class UpdateUserRole(BaseModel):
    role: str

@router.patch("/users/{user_id}/role")
def update_user_role(user_id: uuid.UUID, body: UpdateUserRole, db=Depends(get_db), user: User = Depends(require_role(ROLE_ADMIN))):
    target = db.get(User, user_id)
    if target is None:
        raise BizError(CODE_NOT_FOUND, f"用户不存在: {user_id}")
    target.role = body.role
    db.flush()
    return ok({"id": str(target.id), "role": target.role})


@router.get("/logs")
def get_logs(
    level: str = Query(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$"),
    lines: int = Query(default=200, ge=1, le=1000),
    _user: User = Depends(require_role(ROLE_ADMIN)),
):
    """日志查看：读应用日志文件尾部，按级别阈值过滤（需配置 LOG_FILE_PATH）。"""
    path = get_settings().log_file_path
    if not path or not os.path.exists(path):
        raise BizError(CODE_DATA_INSUFFICIENT, "日志文件输出未启用（LOG_FILE_PATH 未配置）或日志文件不存在")
    tail = read_log_tail(path, min_level=level, lines=lines)
    return ok({
        "items": tail["items"],
        "matched": tail["matched"],
        "truncated": tail["truncated"],
        "level": level,
        "log_file": path,
    })


class LicenseEnrollInput(BaseModel):
    code: str = Field(min_length=1, max_length=2000)


@router.get("/license")
def license_status(db=Depends(get_db), _user: User = Depends(require_role(ROLE_ADMIN))):
    """许可状态：含到期 30/7/1 天三级提醒级别与只读标记。"""
    return ok(license_service.license_status(license_service.get_current_license(db), datetime.now(UTC)))


@router.post("/license")
def enroll_license(body: LicenseEnrollInput, db=Depends(get_db), user: User = Depends(require_role(ROLE_ADMIN))):
    """录入授权码：HMAC 验签后登记（库内只存哈希），同码重复录入幂等。"""
    row = license_service.enroll_license(db, body.code, get_settings().license_secret_key, user.id)
    db.commit()
    return ok(license_service.license_status(row, datetime.now(UTC)), message="授权码已录入")


@router.post("/diagnostics")
def export_diagnostics(db=Depends(get_db), _user: User = Depends(require_role(ROLE_ADMIN))):
    """一键诊断包（T5.13）：脱敏配置 + 近期日志 + 健康检查 + DB 表计数打包 zip。"""
    content = diagnostics_service.build_diagnostics_zip(db)
    filename = f"diagnostics_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.zip"
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


__all__ = ["router"]
