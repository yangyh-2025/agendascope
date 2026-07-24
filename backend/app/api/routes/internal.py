"""内部接口（不走 /api/v1，仅部署内网采集端可达，独立内部 token 鉴权）。

POST /internal/collect — 采集数据接入（详细设计 1.13）
"""
from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import CODE_UNAUTHORIZED, BizError, ok
from app.db.queue import StreamQueue
from app.db.redis_client import get_cache_redis, get_stream_redis
from app.db.session import get_db
from app.schemas.collect import CollectedPayload
from app.services.collect_service import CollectService

router = APIRouter(tags=["internal"])
_bearer = HTTPBearer(auto_error=False)


def verify_internal_token(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    settings = get_settings()
    if credentials is None or credentials.credentials != settings.collector_internal_token:
        raise BizError(CODE_UNAUTHORIZED, "内部 token 缺失或非法")


@router.post("/internal/collect")
def collect(
    payload: CollectedPayload,
    db: Session = Depends(get_db),
    _: None = Depends(verify_internal_token),
):
    service = CollectService(
        db,
        redis_client=get_cache_redis(),
        queue=StreamQueue(get_stream_redis()),
    )
    result = service.ingest(payload)
    return ok(result)
