"""LLM 异步批处理队列（T2.12）。

采集/NLP 主链路只投递任务（O(1) 入队即返回 Future），推理由后台 worker
协程聚合小批次后放到独立线程执行，不阻塞主链路。语义与项目既有
Redis Streams 解耦风格一致；LLM 调用为进程内计算，用 asyncio.Queue
即可，不引入额外队列组件。
"""
import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.llm.settings import LLMSettings, get_llm_settings

logger = structlog.get_logger(__name__)


@dataclass
class LLMJob:
    """一个待执行的 LLM 任务。payload 由 handler 解释。"""

    task_type: str
    payload: dict[str, Any]
    future: asyncio.Future = field(init=False)

    def __post_init__(self) -> None:
        self.future = asyncio.get_running_loop().create_future()


class LLMTaskQueue:
    """异步批处理队列：submit 非阻塞，后台 worker 批量消费。"""

    def __init__(self, settings: LLMSettings | None = None):
        self.settings = settings or get_llm_settings()
        self._queue: asyncio.Queue[LLMJob] | None = None
        self._worker: asyncio.Task | None = None
        self._handler: Callable[[list[LLMJob]], Awaitable[None]] | None = None
        self._processed = 0
        self._failed = 0

    @property
    def stats(self) -> dict[str, int]:
        pending = self._queue.qsize() if self._queue is not None else 0
        return {"pending": pending, "processed": self._processed, "failed": self._failed}

    async def start(self, handler: Callable[[list[LLMJob]], Awaitable[None]]) -> None:
        """启动后台 worker；handler 接收一批任务并负责兑现各 future。"""
        if self._worker is not None:
            return
        self._queue = asyncio.Queue(maxsize=self.settings.queue_maxsize)
        self._handler = handler
        self._worker = asyncio.create_task(self._run(), name="llm-task-worker")

    async def stop(self) -> None:
        if self._worker is None:
            return
        self._worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._worker
        self._worker = None
        self._queue = None

    def submit(self, task_type: str, payload: dict[str, Any]) -> asyncio.Future:
        """投递任务，立即返回 Future（主链路不等待推理完成）。"""
        if self._queue is None:
            raise RuntimeError("LLMTaskQueue 未启动")
        job = LLMJob(task_type=task_type, payload=payload)
        self._queue.put_nowait(job)
        return job.future

    async def _run(self) -> None:
        assert self._queue is not None and self._handler is not None
        batch_size = self.settings.queue_batch_size
        window = self.settings.queue_batch_window_ms / 1000.0
        while True:
            first = await self._queue.get()
            batch = [first]
            # 小窗口内聚合更多任务成一个批次
            deadline = time.monotonic() + window
            while len(batch) < batch_size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(self._queue.get(), timeout=remaining))
                except TimeoutError:
                    break
            try:
                await self._handler(batch)
                self._processed += len(batch)
            except Exception as exc:
                self._failed += len(batch)
                logger.error("llm_batch_failed", size=len(batch), error=str(exc)[:300])
                for job in batch:
                    if not job.future.done():
                        job.future.set_exception(exc)
