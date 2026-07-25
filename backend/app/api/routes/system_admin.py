"""系统管理后台 API（T5.10）：系统概览/用户管理/日志查看/许可管理。"""
import os
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select, text

from app.api.deps import ROLE_ADMIN, require_role, get_db
from app.core.errors import CODE_NOT_FOUND, BizError, ok
from app.db.redis_client import get_cache_redis
from app.models.article import Article
from app.models.collection import CollectionJob
from app.models.topic import Topic
from app.models.user import User

router = APIRouter()


@router.get("/overview")
def system_overview(_user: User = Depends(require_role(ROLE_ADMIN)), db=Depends(get_db), redis_client=Depends(get_cache_redis)):
    """系统概览：CPU/内存/磁盘、当日采集量、队列积压、延迟 P95。"""
    import shutil
    mem_info = {}
    cpu_info = {}
    try:
        import psutil
        mem_info = {"total_mb": int(psutil.virtual_memory().total / 1024**2), "available_mb": int(psutil.virtual_memory().available / 1024**2), "percent": psutil.virtual_memory().percent}
        cpu_info = {"cores": psutil.cpu_count(), "percent": psutil.cpu_percent(interval=0.1)}
    except ImportError:
        mem_info = {"total_mb": 0, "available_mb": 0, "percent": 0}
        cpu_info = {"cores": 1, "percent": 0}

    disk_gb = 0.0
    try:
        disk_gb = round(shutil.disk_usage("/").free / 1024**3, 1)
    except Exception:
        pass

    from datetime import UTC, datetime, timedelta
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0)
    article_count_today = db.scalar(select(func.count()).select_from(Article).where(Article.published_at >= today)) or 0
    topic_count = db.scalar(select(func.count()).select_from(Topic).where(Topic.lifecycle_state != "archived")) or 0
    user_count = db.scalar(select(func.count()).select_from(User)) or 0

    queue_backlog = 0
    try:
        queue_backlog = redis_client.xlen("raw:articles") or 0
    except Exception:
        pass

    return ok({
        "cpu": cpu_info, "memory": mem_info, "disk_free_gb": disk_gb,
        "articles_today": article_count_today, "active_topics": topic_count,
        "users": user_count, "queue_backlog_raw_articles": queue_backlog,
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


@router.get("/license")
def license_status():
    return ok({"status": "active", "expires_at": None, "note": "社区版无许可到期限制；企业版请录入授权码"})


__all__ = ["router"]
