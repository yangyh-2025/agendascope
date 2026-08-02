"""多模型 LLM 推理池：调度 + per-model 限流 + 失败转移 + 熔断。

背景：单模型（讯飞星辰 QPS2/并发2）吞吐受限，且多 worker 并发争抢会触发
并发超限降级。用户提供多个免费推理模型（SiliconFlow / 智谱 / 讯飞星辰），
不同模型不同限流（RPM/并发），同账号不同模型互不影响。

设计：
- per-model：DistributedSemaphore（跨进程并发）+ 令牌桶（QPS，进程内节流，
  per-model 独立——互不影响）
- 调度：每请求选"当前最空闲"模型（in-flight 最少 → QPS 余量最多），均匀分散
- 熔断：单模型连续失败 ≥threshold 次 → 冷却 cooldown_s，冷却期请求转其他模型
- 失败转移：选中模型调用失败 → 自动试其他可用模型（每模型至多试 1 次）
- 全部不可用 → 抛 LLMUnavailableError（触发上层既有降级链）

模型池配置（.env，JSON 数组，不入库）：
  LLM_POOL='[{"name":"sf-glm","base_url":"...","api_key":"...","model":"THUDM/GLM-4-9B-0414","max_concurrency":8,"qps":10}, ...]'
未配置 LLM_POOL 时退化为单模型（兼容现有 LLM_API_* 配置）。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.distributed_semaphore import DistributedSemaphore
from app.llm.settings import LLMSettings, get_llm_settings

logger = logging.getLogger(__name__)


@dataclass
class PoolModel:
    """单个模型条目（配置 + 运行时限流/熔断状态）。"""

    name: str
    base_url: str
    api_key: str
    model: str
    max_concurrency: int = 2
    qps: float = 2.0

    # 运行时状态
    sem: DistributedSemaphore = field(default=None, repr=False)
    in_flight: int = 0
    _inflight_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # 令牌桶（进程内 QPS 节流；跨进程由并发信号量兜底，QPS 偏保守）
    _tokens: float = 0.0
    _last_refill: float = 0.0
    _qps_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # 熔断状态
    consecutive_failures: int = 0
    cooldown_until: float = 0.0

    def __post_init__(self) -> None:
        self.sem = DistributedSemaphore(f"llm:{self.name}", max(1, self.max_concurrency))
        self._tokens = float(self.qps)  # 初始满桶
        self._last_refill = time.monotonic()

    # ---- QPS 令牌桶（进程内节流）----
    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self.qps, self._tokens + elapsed * self.qps)
            self._last_refill = now

    def acquire_qps_token(self, timeout: float = 30.0) -> bool:
        """获取一个 QPS 令牌（阻塞至多 timeout）。返回是否获得。"""
        deadline = time.monotonic() + timeout
        while True:
            with self._qps_lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                if time.monotonic() >= deadline:
                    return False
                wait = min(0.05, max(0.01, (1.0 - self._tokens) / self.qps))
            # 锁外 sleep（避免持锁阻塞其他线程补桶）
            time.sleep(wait)

    # ---- in-flight 计数（调度用）----
    def enter(self) -> None:
        with self._inflight_lock:
            self.in_flight += 1

    def leave(self) -> None:
        with self._inflight_lock:
            self.in_flight = max(0, self.in_flight - 1)

    def available(self, now: float) -> bool:
        """模型当前可用：未熔断且未冷却。"""
        return now >= self.cooldown_until

    def record_failure(self, now: float, cooldown_s: float) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= 3:  # 连续 3 次失败 → 熔断冷却
            self.cooldown_until = now + cooldown_s
            logger.warning(
                "llm_model_circuit_open",
                extra={"model": self.name, "cooldown_s": cooldown_s},
            )
            self.consecutive_failures = 0  # 冷却结束后重新计数

    def record_success(self) -> None:
        self.consecutive_failures = 0


class ModelPool:
    """多模型池：调度 + 失败转移 + 熔断。线程安全（多 worker 共享）。"""

    def __init__(
        self,
        settings: LLMSettings | None = None,
        models: list[PoolModel] | None = None,
        *,
        cooldown_s: float = 60.0,
        failure_threshold: int = 3,
        total_concurrency: int | None = None,
    ):
        self.settings = settings or get_llm_settings()
        self.cooldown_s = cooldown_s
        self.failure_threshold = failure_threshold
        self._lock = threading.Lock()
        self.pool_configured = models is not None or bool(self.settings.pool)
        self._models: list[PoolModel] = models if models is not None else self._load_from_settings(self.settings)
        if not self._models:
            # 兜底：用现有单模型配置（LLM_API_*）
            self._models = [self._legacy_single()]
        # 全局并发闸门：跨模型共享总并发（默认=各模型之和，可显式配置上限）。
        # 多模型时综合并发=各模型并发之和，效率最大化；单模型时即其自身并发。
        self.total_concurrency = (
            total_concurrency
            or sum(m.max_concurrency for m in self._models)
            or max(1, self.settings.max_concurrency)
        )
        self._global_sem = DistributedSemaphore("llm:pool", max(1, self.total_concurrency))
        self._last_success: dict[str, float] = {}

    @staticmethod
    def _load_from_settings(settings: LLMSettings) -> list[PoolModel]:
        raw = settings.pool
        if not raw:
            return []
        try:
            entries = json.loads(raw)
        except json.JSONDecodeError:
            logger.error("llm_pool_invalid_json", extra={"raw": raw[:200]})
            return []
        models = []
        for e in entries:
            try:
                models.append(
                    PoolModel(
                        name=str(e["name"]),
                        base_url=str(e["base_url"]).rstrip("/"),
                        api_key=str(e.get("api_key", "")),
                        model=str(e["model"]),
                        max_concurrency=int(e.get("max_concurrency", 2)),
                        qps=float(e.get("qps", 2.0)),
                    )
                )
            except (KeyError, ValueError) as exc:
                logger.error("llm_pool_bad_entry", extra={"entry": e, "error": str(exc)})
        return models

    def _legacy_single(self) -> PoolModel:
        s = self.settings
        return PoolModel(
            name=s.api_model or "default",
            base_url=s.api_base_url,
            api_key=s.api_key,
            model=s.api_model or "default",
            max_concurrency=max(1, s.max_concurrency),
            qps=max(1.0, s.max_concurrency),
        )

    @property
    def models(self) -> list[PoolModel]:
        return self._models

    # ---- 调度 ----
    def _pick_model(self, now: float) -> PoolModel | None:
        """选最空闲的可用模型（in-flight 最少 → QPS 余量最大）。"""
        available = [m for m in self._models if m.available(now)]
        if not available:
            return None
        return min(available, key=lambda m: (m.in_flight, -m._tokens))

    def acquire(self) -> tuple[PoolModel, bool]:
        """选一个可用模型并获取并发+QPS 配额。

        顺序：全局并发闸门 → 模型 QPS 令牌 → 模型分布式并发（此顺序避免 ABBA 死锁：
        线程先拿全局闸门再等模型信号量，不会出现"持有模型信号量等全局闸门"）。
        返回 (model, ok)。ok=False 表示暂无可用配额/全部熔断——调用方等待重试。
        """
        now = time.monotonic()
        with self._lock:
            model = self._pick_model(now)
            if model is None:
                return None, False
            # 先拿全局闸门（跨模型总并发）
            self._global_sem.acquire()
            # 再拿模型 QPS 令牌（进程内节流）
            if not model.acquire_qps_token():
                self._global_sem.release()
                return None, False
            model.sem.acquire()
            model.enter()
            return model, True

    def release(self, model: PoolModel, *, success: bool) -> None:
        model.leave()
        model.sem.release()
        self._global_sem.release()
        now = time.monotonic()
        if success:
            model.record_success()
            self._last_success[model.name] = now
        else:
            model.record_failure(now, self.cooldown_s)


__all__ = ["ModelPool", "PoolModel"]
