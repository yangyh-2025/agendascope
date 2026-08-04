"""API Key 鉴权与限流（数据开放平台 X-API-Key 头）。

Key 格式: agk_<urlsafe_base64 32 字节>，创建时完整返回一次，落库只存 sha256 hash。
限流: Redis token bucket，key=f"ratelimit:apikey:{key_id}"，每分钟窗口。
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import CODE_RATE_LIMITED, CODE_UNAUTHORIZED, BizError
from app.db.redis_client import get_cache_redis
from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.user import User

KEY_PREFIX = "agk_"


def generate_api_key() -> tuple[str, str, str]:
    """生成 (plain_key, key_hash, prefix)。
    plain_key 只在创建时返回一次，之后服务端不再可见。
    prefix 用于 UI 显示（agk_ + 6 位随机）。
    """
    raw = secrets.token_urlsafe(32)  # 43 字符
    plain = f"{KEY_PREFIX}{raw}"
    key_hash = hashlib.sha256(plain.encode("utf-8")).hexdigest()
    prefix = plain[:10]  # 'agk_' + 6 字符
    return plain, key_hash, prefix


def hash_api_key(plain_key: str) -> str:
    return hashlib.sha256(plain_key.encode("utf-8")).hexdigest()


def _check_rate_limit(api_key: ApiKey) -> None:
    """Redis INCR + EXPIRE 简单窗口限流；Redis 异常静默放行。"""
    try:
        r = get_cache_redis()
        bucket = f"ratelimit:apikey:{api_key.id}"
        n = r.incr(bucket)
        if n == 1:
            r.expire(bucket, 60)
        if n > api_key.rate_limit_per_minute:
            raise BizError(
                CODE_RATE_LIMITED,
                f"超出限流 {api_key.rate_limit_per_minute}/分钟，请稍后再试",
            )
    except BizError:
        raise
    except Exception:  # noqa: BLE001 Redis 不可用静默放行（低内存服务器）
        return


def get_api_key_user(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> User:
    """X-API-Key 鉴权：供 /api/v1/open/* 开放接口使用。

    - 校验 Key 存在/未吊销/未过期
    - 限流
    - 更新 last_used_at（每 60s 至多一次，减少写压力）
    - 返回 Key 所属 User（status 必须 active）
    """
    if not x_api_key:
        raise BizError(CODE_UNAUTHORIZED, "缺少 X-API-Key 请求头")
    key_hash = hash_api_key(x_api_key)
    api_key = db.scalar(select(ApiKey).where(ApiKey.key_hash == key_hash))
    if api_key is None:
        raise BizError(CODE_UNAUTHORIZED, "API Key 无效")
    if api_key.revoked_at is not None:
        raise BizError(CODE_UNAUTHORIZED, "API Key 已吊销")
    now = datetime.now(UTC)
    if api_key.expires_at is not None and api_key.expires_at < now:
        raise BizError(CODE_UNAUTHORIZED, "API Key 已过期")

    _check_rate_limit(api_key)

    # 更新 last_used_at（节流：>60s 才写一次）
    last = api_key.last_used_at
    if last is None or (now - last).total_seconds() > 60:
        api_key.last_used_at = now
        db.add(api_key)
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

    user = db.get(User, api_key.user_id)
    if user is None or user.status != "active":
        raise BizError(CODE_UNAUTHORIZED, "API Key 所属用户不可用")
    request.state.api_key = api_key
    request.state.user = user
    return user
