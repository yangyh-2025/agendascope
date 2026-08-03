"""Redis 客户端（db0 缓存/去重，db1 Streams 队列）。

socket_timeout=60：低内存/慢速部署下（进程调度延迟、swap 兜底），Redis 操作可能
瞬时超时导致 worker 崩溃；放宽超时让 worker 在重负载下保持存活（配合 restart 策略自愈）。
"""
import redis

from app.config import get_settings

_cache_client = None
_stream_client = None

_SOCKET_TIMEOUT = 60


def get_cache_redis() -> redis.Redis:
    global _cache_client
    if _cache_client is None:
        _cache_client = redis.Redis.from_url(
            get_settings().redis_url, decode_responses=True, socket_timeout=_SOCKET_TIMEOUT
        )
    return _cache_client


def get_stream_redis(force_new: bool = False) -> redis.Redis:
    """返回 db1 客户端；force_new=True 时丢弃旧连接新建（连接损坏自愈用）。

    低内存 3Mbps 部署下 Redis 连接可能 TCP 半开（网络层丢包），worker 捕获
    超时后调 force_new 换全新连接，避免持续超时。
    """
    global _stream_client
    if force_new or _stream_client is None:
        _stream_client = redis.Redis.from_url(
            get_settings().redis_stream_url,
            decode_responses=True,
            socket_timeout=_SOCKET_TIMEOUT,
            # 网络抖动/连接半开时自动重连：低内存 3Mbps 带宽下 Redis 响应可能瞬时超时，
            # 默认不重连会让 worker 崩溃循环；开启后超时自动重连，可靠性显著提升
            retry_on_timeout=True,
            socket_keepalive=True,
        )
    return _stream_client


def reset_clients() -> None:
    """测试隔离用：丢弃缓存的单例客户端。"""
    global _cache_client, _stream_client
    _cache_client = None
    _stream_client = None
