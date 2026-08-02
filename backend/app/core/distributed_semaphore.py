"""跨进程分布式信号量（Redis 实现）——解决多 worker 并发争抢 LLM 配额。

背景：detection-worker 与 naming-worker 是两个独立进程，各自持有线程信号量
（LLM_MAX_CONCURRENCY），互不可见。两个 worker 同时打 LLM API 时，进程内
信号量无法约束跨进程总并发，超讯飞星辰"并发 2"硬配额触发
AppIdConcurrencyOverFlowError → 降级。

本模块用 Redis 原子 INCR 实现租约式信号量：
  acquire(): INCR key，若 > max_leases 则 DECR 回滚并等待重试（带超时）
  release(): DECR key

租约超时保护：key 带 EX 过期（lease_seconds），进程崩溃不会永久占坑；
acquire 时若发现 key 无 TTL 或已过期则重置计数（防陈旧计数）。

Redis 不可用时退化为本地 threading.Semaphore（进程内仍限流，不阻塞主链路）。
"""
from __future__ import annotations

import threading
import time

from app.db.redis_client import get_cache_redis

_LEASE_SECONDS = 30


class DistributedSemaphore:
    """跨进程信号量：Redis 租约 + 本地线程信号量双保险。"""

    def __init__(self, name: str, max_leases: int, acquire_timeout: float = 60.0):
        self._key = f"sem:{name}"
        self._max_leases = max(1, max_leases)
        self._acquire_timeout = acquire_timeout
        # 本地信号量：Redis 不可用时兜底（进程内仍限流）
        self._local = threading.Semaphore(max(1, max_leases))
        self._redis_available = True
        self._redis_probed = False  # 首次探测失败后永久降级本地，不再反复打 Redis

    def _redis_ok(self) -> bool:
        """Redis 可用性探测：首次失败永久降级（避免无 Redis 时每个请求卡 60s）。

        探测用独立短超时客户端（不污染全局单例的 60s socket_timeout）。
        """
        if not self._redis_probed:
            try:
                import redis as _redis

                from app.config import get_settings

                probe = _redis.Redis.from_url(
                    get_settings().redis_url,
                    socket_connect_timeout=2,
                    socket_timeout=2,
                )
                probe.ping()
                probe.close()
                self._redis_available = True
            except Exception:  # noqa: BLE001
                self._redis_available = False
            self._redis_probed = True
        return self._redis_available

    def _try_acquire_redis(self) -> bool:
        """原子 INCR 尝试获取租约；超限回滚。返回是否成功。"""
        try:
            r = get_cache_redis()
            # 租约清理：计数过陈旧（key 无 TTL 或已超 lease 时长）则重置
            ttl = r.ttl(self._key)
            if ttl == -1:  # 无过期时间（异常残留）→ 重置
                r.delete(self._key)
            elif ttl == -2:  # key 不存在
                pass
            count = r.incr(self._key)
            if count <= self._max_leases:
                r.expire(self._key, _LEASE_SECONDS)
                return True
            r.decr(self._key)
            return False
        except Exception:  # noqa: BLE001 Redis 不可用：退本地信号量
            self._redis_available = False
            return False

    def _release_redis(self) -> None:
        try:
            r = get_cache_redis()
            r.decr(self._key)
            # 计数为 0 时清 key（避免 -1 残留）
            if int(r.get(self._key) or 0) <= 0:
                r.delete(self._key)
        except Exception:  # noqa: BLE001
            pass

    def acquire(self) -> None:
        """阻塞直到获得租约（Redis 租约 + 本地信号量双保险）。"""
        # 本地信号量总是先拿（进程内串行），再尝试 Redis 租约
        self._local.acquire()
        if not self._redis_ok():
            return
        deadline = time.monotonic() + self._acquire_timeout
        while True:
            if self._try_acquire_redis():
                return
            if time.monotonic() >= deadline:
                # 超时：放弃 Redis 租约（本地信号量仍持有，进程内串行即可）
                return
            time.sleep(0.1)

    def release(self) -> None:
        if self._redis_available:
            self._release_redis()
        self._local.release()

    # context manager 协议（with sem: 用法，与 threading.Semaphore 兼容）
    def __enter__(self) -> "DistributedSemaphore":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


__all__ = ["DistributedSemaphore"]
