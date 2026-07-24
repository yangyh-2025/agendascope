"""LLM 推理健康监控与降级判定（T2.12/T2.16，详细设计 6.2）。

规则：滑动窗口内推理失败/超时率 >20%（且样本数达标）→ 判定降级，
降级期间 annotator 走 c-TF-IDF 兜底；窗口内失败率回落到阈值以下 → 自动恢复。
模型加载失败立即判降级。绝不静默降级：状态翻转由 annotator 写 P1 告警 + WARN 日志。
"""
import threading
from collections import deque
from datetime import UTC, datetime

import structlog

from app.llm.settings import LLMSettings, get_llm_settings

logger = structlog.get_logger(__name__)


class DegradationMonitor:
    def __init__(self, settings: LLMSettings | None = None):
        self.settings = settings or get_llm_settings()
        self._lock = threading.Lock()
        self._outcomes: deque[bool] = deque(maxlen=self.settings.health_window_size)
        self._degraded = False
        self._degraded_since: datetime | None = None
        self._reason = ""

    @property
    def degraded(self) -> bool:
        with self._lock:
            return self._degraded

    @property
    def degraded_since(self) -> datetime | None:
        with self._lock:
            return self._degraded_since

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    def failure_rate(self) -> float:
        with self._lock:
            if not self._outcomes:
                return 0.0
            return 1.0 - sum(self._outcomes) / len(self._outcomes)

    def record(self, success: bool, reason: str = "") -> bool:
        """记录一次推理结果；返回本次调用是否引起了状态翻转（True=刚降级，False=无变化/刚恢复见 recovered 事件）。

        翻转语义通过返回值与 degraded 属性组合判断：返回 True 且 degraded=True → 进入降级；
        返回 True 且 degraded=False → 恢复。
        """
        flipped = False
        with self._lock:
            self._outcomes.append(success)
            if not self._degraded:
                if (
                    len(self._outcomes) >= self.settings.health_min_samples
                    and self.failure_rate() > self.settings.failure_rate_threshold
                ):
                    self._degraded = True
                    self._degraded_since = datetime.now(UTC)
                    self._reason = reason or f"推理失败率 {self.failure_rate():.0%} > {self.settings.failure_rate_threshold:.0%}"
                    flipped = True
            else:
                if (
                    len(self._outcomes) >= self.settings.health_min_samples
                    and self.failure_rate() <= self.settings.failure_rate_threshold
                ):
                    self._degraded = False
                    recovered_since = self._degraded_since
                    self._degraded_since = None
                    self._reason = ""
                    flipped = True
                    logger.warning("degradation_recovered", component="llm_service", since=str(recovered_since))
        if flipped and self._degraded:
            logger.warning(
                "degradation_activated", component="llm_service",
                fallback="ctfidf_fallback", reason=self._reason, since=str(self._degraded_since),
            )
        return flipped

    def mark_unavailable(self, reason: str) -> bool:
        """模型加载失败/服务崩溃 → 立即降级。返回是否状态翻转。"""
        with self._lock:
            if self._degraded:
                return False
            self._degraded = True
            self._degraded_since = datetime.now(UTC)
            self._reason = reason
        logger.warning(
            "degradation_activated", component="llm_service",
            fallback="ctfidf_fallback", reason=reason, since=str(self._degraded_since),
        )
        return True

    def mark_recovered(self) -> bool:
        """人工/探针确认恢复（加载成功后可调用）。返回是否状态翻转。"""
        with self._lock:
            if not self._degraded:
                return False
            self._degraded = False
            self._degraded_since = None
            self._reason = ""
            self._outcomes.clear()
        logger.warning("degradation_recovered", component="llm_service")
        return True
