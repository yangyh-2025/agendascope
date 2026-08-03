"""健康检查：/health 组件探活（DB/Redis/ES）。"""
import redis as redis_lib
from fastapi import APIRouter
from sqlalchemy import text

from app.config import get_settings
from app.core.errors import ok
from app.core.logging import get_logger
from app.db.redis_client import get_cache_redis, get_stream_redis
from app.db.session import get_engine

router = APIRouter(tags=["health"])
logger = get_logger("health")


def _check_db() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("health_db_fail", error=str(exc))
        return False


def _check_redis(client_getter) -> bool:
    try:
        return bool(client_getter().ping())
    except (redis_lib.RedisError, OSError) as exc:
        logger.warning("health_redis_fail", error=str(exc))
        return False


def _check_es() -> bool:
    try:
        import requests

        # ES 为可选组件：短超时（低内存部署可关 ES，DNS 解析失败快速返回 False）
        resp = requests.get(get_settings().elasticsearch_url, timeout=1.5)
        return resp.status_code == 200
    except Exception as exc:  # noqa: BLE001
        logger.warning("health_es_fail", error=str(exc))
        return False


@router.get("/health")
def health():
    components = {
        "postgres": _check_db(),
        "redis_cache": _check_redis(get_cache_redis),
        "redis_stream": _check_redis(get_stream_redis),
        "elasticsearch": _check_es(),
    }
    # 核心组件（PG/Redis）健康即 ok；ES 为可选（低内存部署可关，搜索降级走 PG）
    core_healthy = components["postgres"] and components["redis_cache"] and components["redis_stream"]
    healthy = core_healthy and components["elasticsearch"]
    return ok({"status": "ok" if healthy else "degraded", "components": components})
