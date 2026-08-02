"""Redis 响应缓存（低内存/慢速服务器 API 提速）。

只缓存 GET 列表类成功响应（{code:0}），TTL 短（30s）保证数据新鲜。
key 含用户角色 + 国家权限 + 完整 query 参数，按用户维度隔离避免越权。

失败（Redis 不可用/写异常）静默降级——缓存是锦上添花，绝不阻塞主链路。
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.db.redis_client import get_cache_redis

logger = logging.getLogger(__name__)


def _cache_key(path: str, role: str, country_scope: str, query: str) -> str:
    payload = f"{path}|{role}|{country_scope}|{query}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:40]
    return f"api:cache:{path}|{role}|{country_scope}|{digest}"


def get_cached(path: str, role: str, country_scope: str, query: str) -> tuple[dict | None, bool]:
    """取缓存。返回 (data, from_cache)；缓存缺失或 Redis 异常返回 (None, False)。"""
    try:
        r = get_cache_redis()
        key = _cache_key(path, role, country_scope, query)
        raw = r.get(key)
        if raw is None:
            return None, False
        return json.loads(raw), True
    except Exception:  # noqa: BLE001 缓存不可用静默降级
        return None, False


def set_cached(
    path: str,
    role: str,
    country_scope: str,
    query: str,
    data: Any,
    ttl: int = 30,
) -> None:
    """写缓存。Redis 异常静默降级。"""
    try:
        r = get_cache_redis()
        key = _cache_key(path, role, country_scope, query)
        r.set(key, json.dumps(data, ensure_ascii=False), ex=ttl)
    except Exception:  # noqa: BLE001
        pass


def invalidate_cache(path_prefix: str, ttl: int = 0) -> None:
    """写操作后使缓存失效（按前缀删）。未实现则退化为短 TTL。"""
    try:
        r = get_cache_redis()
        pattern = f"api:cache:{path_prefix}|*"
        keys = [k for k in r.scan_iter(match=pattern) if k]
        if keys:
            r.delete(*keys)
    except Exception:  # noqa: BLE001
        pass
