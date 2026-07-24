"""LLM 异步批处理队列单元测试（T2.12）。"""
import asyncio

import pytest

from app.llm.queue import LLMJob, LLMTaskQueue
from app.llm.settings import LLMSettings

pytestmark = pytest.mark.asyncio


async def test_submit_returns_future_and_handler_resolves():
    queue = LLMTaskQueue(LLMSettings(queue_batch_window_ms=10))
    seen: list[list[str]] = []

    async def handler(batch: list[LLMJob]) -> None:
        seen.append([job.task_type for job in batch])
        for job in batch:
            job.future.set_result(f"done:{job.task_type}")

    await queue.start(handler)
    try:
        future = queue.submit("topic_naming", {"titles": ["t"]})
        assert await asyncio.wait_for(future, timeout=2) == "done:topic_naming"
        assert seen == [["topic_naming"]]
        assert queue.stats["processed"] == 1
    finally:
        await queue.stop()


async def test_batch_aggregation_within_window():
    queue = LLMTaskQueue(LLMSettings(queue_batch_window_ms=100, queue_batch_size=8))
    batch_sizes: list[int] = []

    async def handler(batch: list[LLMJob]) -> None:
        batch_sizes.append(len(batch))
        for job in batch:
            job.future.set_result(job.payload["n"])

    await queue.start(handler)
    try:
        futures = [queue.submit("topic_summary", {"n": i}) for i in range(3)]
        results = await asyncio.gather(*futures)
        assert results == [0, 1, 2]
        assert batch_sizes == [3], "窗口内 3 个任务应聚成 1 个批次"
    finally:
        await queue.stop()


async def test_submit_does_not_block_main_loop():
    queue = LLMTaskQueue(LLMSettings(queue_batch_window_ms=10))
    started = asyncio.Event()

    async def handler(batch: list[LLMJob]) -> None:
        started.set()
        await asyncio.sleep(0.2)  # 模拟慢推理
        for job in batch:
            job.future.set_result("ok")

    await queue.start(handler)
    try:
        future = queue.submit("topic_naming", {})
        # 提交后主链路立即可继续执行，不等待推理
        assert not future.done()
        await started.wait()
        assert await future == "ok"
    finally:
        await queue.stop()


async def test_handler_exception_propagates_to_futures():
    queue = LLMTaskQueue(LLMSettings(queue_batch_window_ms=10))

    async def handler(batch: list[LLMJob]) -> None:
        raise RuntimeError("engine exploded")

    await queue.start(handler)
    try:
        future = queue.submit("topic_naming", {})
        with pytest.raises(RuntimeError, match="engine exploded"):
            await asyncio.wait_for(future, timeout=2)
        assert queue.stats["failed"] == 1
    finally:
        await queue.stop()


async def test_submit_before_start_raises():
    queue = LLMTaskQueue()
    with pytest.raises(RuntimeError):
        queue.submit("topic_naming", {})
