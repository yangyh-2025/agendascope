"""API 响应缓存中间件：GET 列表类接口 Redis 缓存（TTL 30s）。

缓解低内存/慢速服务器下"人物监测/议题页切页后要等好几秒"的问题：
命中缓存直接返回，后端少跑一次 DB 查询；写操作不缓存。

只缓存：
- 只读 GET
- 成功响应（HTTP 200 且 body code==0）
- 列表类路径（/persons-orgs /topics /map/countries /sources /agenda-events /alerts /snapshots）
  列表接口数据量大、查询重，缓存收益最高；详情页命中率低不缓存。

按用户维度隔离（角色 + 国家权限 + query），避免越权/串数据。Redis 异常静默降级。
"""
from __future__ import annotations

import json

from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api.deps import ROLE_AUTHORIZED, ROLE_REGISTERED
from app.config import get_settings
from app.core.response_cache import get_cached, set_cached

# 注册角色默认可见国家（与 topics.py 口径一致，用于缓存 key 隔离）
_REGISTERED_DEFAULT_COUNTRIES = {"CN", "US", "JP"}

# 可缓存路径前缀（列表类，避免缓存详情/写接口）
_CACHEABLE_PREFIXES = (
    "/api/v1/persons-orgs",
    "/api/v1/topics",
    "/api/v1/map/countries",
    "/api/v1/sources",
    "/api/v1/agenda-events",
    "/api/v1/alerts",
    "/api/v1/snapshots",
)

_CACHE_TTL = 30


def _cacheable(path: str, method: str) -> bool:
    return method.upper() == "GET" and path.startswith(_CACHEABLE_PREFIXES)


def _role_and_scope_from_token(request: Request) -> tuple[str, str] | None:
    """从 Authorization Bearer token 解析 role 与缓存作用域。

    BaseHTTPMiddleware 在路由依赖之前执行，request.state.user 尚未设置，
    需直接解码 token 取 role claim；失败（未登录/过期）返回 None → 不缓存。
    """
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(
            token,
            get_settings().jwt_secret_key,
            algorithms=[get_settings().jwt_algorithm],
        )
    except JWTError:
        return None
    role = payload.get("role", "")
    if role == ROLE_REGISTERED:
        scope = "|".join(sorted(_REGISTERED_DEFAULT_COUNTRIES))
    else:
        scope = role  # authorized/admin：全量（内部口径一致）
    return role, scope


class ResponseCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not _cacheable(request.url.path, request.method):
            return await call_next(request)

        parsed = _role_and_scope_from_token(request)
        if parsed is None:
            return await call_next(request)
        role, country_scope = parsed
        query = request.url.query

        # 命中缓存：直接返回
        cached, hit = get_cached(request.url.path, role, country_scope, query)
        if hit:
            resp = JSONResponse(
                status_code=200,
                content=cached,
                headers={"X-Cache": "HIT"},
            )
            return resp

        response = await call_next(request)
        # 仅缓存成功且 code==0 的响应（BizError 等不缓存）
        try:
            if response.status_code == 200:
                body = json.loads(response.body)
                if isinstance(body, dict) and body.get("code") == 0:
                    set_cached(
                        request.url.path,
                        role,
                        country_scope,
                        query,
                        body,
                        ttl=_CACHE_TTL,
                    )
                    response.headers["X-Cache"] = "MISS"
        except Exception:  # noqa: BLE001 缓存失败不影响响应
            pass
        return response


__all__ = ["ResponseCacheMiddleware"]
