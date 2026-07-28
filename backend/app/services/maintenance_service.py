"""磁盘超阈值自动清理（T5.10）：磁盘占用 >85% 时删除 90 天前原始 HTML/缓冲文件并写告警。

调度接线（collector 归另一工作流，不在本模块改动）：在调度器周期任务中调用
`run_disk_cleanup(db)`（建议每日一次），或独立脚本 cron 调用。
清理目标：RAW_HTML_DIR 原始 HTML 存储目录 + GDELT 本地缓冲目录下的过期文件；
数据库业务数据一律不动。
"""
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import get_logger
from app.models.alert import Alert
from app.services.seed_service import ensure_admin, ensure_system_rules

logger = get_logger("maintenance")


def disk_usage_percent(path: str = "/") -> float:
    """磁盘占用百分比（shutil 标准库实现，不依赖 psutil）。"""
    usage = shutil.disk_usage(path)
    return round(usage.used / usage.total * 100, 1)


def find_expired_files(directories: list[str], retention_days: int, now: datetime) -> list[Path]:
    """列出目录下 mtime 早于保留窗口的常规文件（不递归子目录，不跟随符号链接）。"""
    cutoff = now.timestamp() - retention_days * 86400
    expired: list[Path] = []
    for directory in directories:
        root = Path(directory)
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            try:
                if entry.is_file() and not entry.is_symlink() and entry.stat().st_mtime < cutoff:
                    expired.append(entry)
            except OSError as exc:
                logger.warning("disk_cleanup_stat_error", path=str(entry), error=str(exc))
    return expired


def cleanup_expired_files(directories: list[str], retention_days: int, now: datetime) -> dict:
    """删除过期文件，返回 {deleted, freed_bytes, errors}。"""
    deleted = 0
    freed = 0
    errors: list[str] = []
    for entry in find_expired_files(directories, retention_days, now):
        try:
            freed += entry.stat().st_size
            entry.unlink()
            deleted += 1
        except OSError as exc:
            errors.append(f"{entry}: {exc}")
            logger.error("disk_cleanup_delete_error", path=str(entry), error=str(exc))
    return {"deleted": deleted, "freed_bytes": freed, "errors": errors}


def run_disk_cleanup(db: Session, now: datetime | None = None) -> dict:
    """磁盘占用超阈值时清理过期原始文件并写站内告警；未超阈值只报告不动作。"""
    settings = get_settings()
    now = now or datetime.now(UTC)
    percent = disk_usage_percent("/")
    result = {
        "triggered": False,
        "disk_percent": percent,
        "threshold_percent": settings.disk_cleanup_threshold_percent,
        "deleted": 0,
        "freed_bytes": 0,
        "errors": [],
    }
    if percent < settings.disk_cleanup_threshold_percent:
        logger.info("disk_cleanup_skip", disk_percent=percent)
        return result

    stats = cleanup_expired_files(
        [settings.raw_html_dir, settings.gdelt_buffer_dir],
        settings.raw_html_retention_days,
        now,
    )
    result.update(stats)
    result["triggered"] = True
    logger.warning("disk_cleanup_done", disk_percent=percent, **stats)

    # 告警闭环：写入系统源健康规则下的站内告警（管理后台/预警中心可见）
    admin = ensure_admin(db)
    rule = ensure_system_rules(db, admin)
    db.add(Alert(
        rule_id=rule.id,
        user_id=admin.id,
        payload={
            "type": "disk_cleanup",
            "disk_percent": percent,
            "threshold_percent": settings.disk_cleanup_threshold_percent,
            "retention_days": settings.raw_html_retention_days,
            "deleted": stats["deleted"],
            "freed_bytes": stats["freed_bytes"],
            "error_count": len(stats["errors"]),
        },
    ))
    db.flush()
    return result
