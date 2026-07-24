"""Redis 客户端（db0 缓存/去重，db1 Streams 队列）。"""
import redis

from app.config import get_settings

_cache_client = None
_stream_client = None


def get_cache_redis() -> redis.Redis:
    global _cache_client
    if _cache_client is None:
        _cache_client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _cache_client


def get_stream_redis() -> redis.Redis:
    global _stream_client
    if _stream_client is None:
        _stream_client = redis.Redis.from_url(get_settings().redis_stream_url, decode_responses=True)
    return _stream_client


def reset_clients() -> None:
    """测试隔离用：丢弃缓存的单例客户端。"""
    global _cache_client, _stream_client
    _cache_client = None
    _stream_client = None
