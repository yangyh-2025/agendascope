"""基于 Redis 的会话态：refresh 白名单（轮换）、access 黑名单、登录限流。"""
import time

import redis

from app.config import get_settings

_K_REFRESH = "session:refresh:{user_id}:{jti}"
_K_ACCESS_BL = "bl:access:{jti}"
_K_LOGIN_RL = "rl:login:{ip}"


def _client() -> redis.Redis:
    from app.db.redis_client import get_cache_redis

    return get_cache_redis()


def register_refresh_session(user_id: str, jti: str, expires_seconds: int) -> None:
    _client().setex(_K_REFRESH.format(user_id=user_id, jti=jti), expires_seconds, "1")


def is_refresh_session_valid(user_id: str, jti: str) -> bool:
    return bool(_client().exists(_K_REFRESH.format(user_id=user_id, jti=jti)))


def revoke_refresh_session(user_id: str, jti: str) -> None:
    _client().delete(_K_REFRESH.format(user_id=user_id, jti=jti))


def revoke_all_user_sessions(user_id: str) -> None:
    client = _client()
    pattern = _K_REFRESH.format(user_id=user_id, jti="*")
    keys = list(client.scan_iter(match=pattern, count=200))
    if keys:
        client.delete(*keys)


def blacklist_access_token(jti: str, exp_epoch: int) -> None:
    ttl = max(int(exp_epoch - time.time()), 1)
    _client().setex(_K_ACCESS_BL.format(jti=jti), ttl, "1")


def is_access_blacklisted(jti: str) -> bool:
    return bool(_client().exists(_K_ACCESS_BL.format(jti=jti)))


def hit_login_rate_limit(ip: str) -> bool:
    """同一 IP 登录限流（默认 10 次/分钟）；超限返回 True。"""
    settings = get_settings()
    client = _client()
    key = _K_LOGIN_RL.format(ip=ip)
    count = client.incr(key)
    if count == 1:
        client.expire(key, 60)
    return count > settings.login_rate_limit_per_minute
