"""一键诊断包（T5.13）：脱敏配置快照 + 近期日志 + 健康检查 + DB 表计数打包 zip。

脱敏口径：键名含 password/secret/token/api_key/credential 的值一律替换为 ***；
URL 中的 user:password@ 凭据段同样脱敏。授权码哈希、JWT 密钥等绝不外泄明文。
"""
import io
import json
import re
import zipfile
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import get_logger
from app.services.log_service import read_log_tail

logger = get_logger("diagnostics")

_SENSITIVE_KEY_RE = re.compile(r"password|secret|token|api_?key|credential|private", re.IGNORECASE)
_URL_CREDENTIAL_RE = re.compile(r"(://[^:/@\s]*:)[^@\s]+(@)")

_DIAG_LOG_LINES = 500


def redact_config(config: dict) -> dict:
    """配置字典递归脱敏：敏感键值 → ***；URL 内嵌凭据 → 用户:***@。"""
    redacted: dict = {}
    for key, value in config.items():
        if _SENSITIVE_KEY_RE.search(str(key)):
            redacted[key] = "***"
        elif isinstance(value, dict):
            redacted[key] = redact_config(value)
        elif isinstance(value, str):
            redacted[key] = _URL_CREDENTIAL_RE.sub(r"\1***\2", value)
        else:
            redacted[key] = value
    return redacted


def config_snapshot() -> dict:
    """当前生效配置（pydantic Settings 全量）脱敏快照。"""
    return redact_config(get_settings().model_dump())


def health_snapshot() -> dict:
    """复用 /health 的组件探活，避免两套口径。"""
    from app.api.routes.health import _check_db, _check_es, _check_redis
    from app.db.redis_client import get_cache_redis, get_stream_redis

    components = {
        "postgres": _check_db(),
        "redis_cache": _check_redis(get_cache_redis),
        "redis_stream": _check_redis(get_stream_redis),
        "elasticsearch": _check_es(),
    }
    return {"status": "ok" if all(components.values()) else "degraded", "components": components}


def table_counts(db: Session) -> dict:
    """核心业务表行数快照（诊断规模与数据滞留用）。"""
    from app.models.alert import Alert
    from app.models.article import Article
    from app.models.audit import AuditLog
    from app.models.collection import CollectionJob
    from app.models.source import Source
    from app.models.system_state import SetupState, SystemLicense
    from app.models.topic import Topic
    from app.models.user import User

    tables = {
        "sources": Source,
        "articles": Article,
        "topics": Topic,
        "users": User,
        "alerts": Alert,
        "audit_logs": AuditLog,
        "collection_jobs": CollectionJob,
        "setup_state": SetupState,
        "system_license": SystemLicense,
    }
    return {
        name: db.scalar(select(func.count()).select_from(model)) or 0
        for name, model in tables.items()
    }


def build_diagnostics_zip(db: Session) -> bytes:
    """生成诊断包 zip：meta/config/health/db_counts + 近期日志（若启用文件输出）。"""
    settings = get_settings()
    generated_at = datetime.now(UTC)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps({
            "generated_at": generated_at.isoformat(),
            "app_name": settings.app_name,
            "app_env": settings.app_env,
        }, ensure_ascii=False, indent=2))
        zf.writestr(
            "config_snapshot.json",
            json.dumps(config_snapshot(), ensure_ascii=False, indent=2, default=str),
        )
        try:
            health = health_snapshot()
        except Exception as exc:  # noqa: BLE001 —— 诊断包不因单组件失败而整体失败
            logger.warning("diagnostics_health_error", error=str(exc))
            health = {"status": "error", "error": str(exc)}
        zf.writestr("health.json", json.dumps(health, ensure_ascii=False, indent=2))
        zf.writestr(
            "db_counts.json",
            json.dumps(table_counts(db), ensure_ascii=False, indent=2),
        )
        if settings.log_file_path:
            try:
                tail = read_log_tail(settings.log_file_path, min_level="DEBUG", lines=_DIAG_LOG_LINES)
                zf.writestr("recent_logs.txt", "\n".join(tail["items"]))
            except OSError as exc:
                logger.warning("diagnostics_log_read_error", error=str(exc))
                zf.writestr("recent_logs.txt", f"日志文件读取失败: {exc}")
    return buf.getvalue()
