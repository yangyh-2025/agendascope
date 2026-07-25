"""命名 worker 进程入口：python -m app.worker.naming_worker（M2-3 LLM 服务接线）。

聚类管线（cluster worker 在线归簇 / 每小时重聚类校正）产出的议题先以
ctfidf_fallback/keyword_fallback 兜底命名留痕（app.clustering.service
list_pending_naming 即待命名队列）。本 worker 轮询该队列，经 LLMTaskQueue
异步批处理调用 TopicAnnotator 完成命名+分类+摘要，再用
ClusterService.record_llm_naming 回填 topics（naming_method=llm 留痕，
人工锁定字段不覆盖），每条判定写 llm_judgements（M2-3 关键不变量）。

降级语义（绝不静默）：LLM 不可用/推理失败率超标 → annotator 走既有
ctfidf_fallback 兜底链路，议题保持兜底命名留在待命名队列，写 P1 告警
（1h 防抖）；worker 每轮先做恢复探针（模型加载 + 一次真实命名推理），
探针通过则 mark_recovered 并调 backfill_degraded_topics 回填降级期议题。
"""
import argparse
import asyncio
import time
import uuid
from typing import Any

from app.clustering.service import ClusterDossier, ClusterService
from app.config import get_settings
from app.core.logging import configure_logging, get_logger, new_trace_id, set_trace_id
from app.db.redis_client import get_cache_redis
from app.db.session import get_session_factory
from app.llm import prompts
from app.llm.alerting import write_llm_degraded_alert
from app.llm.annotator import TopicAnnotator
from app.llm.errors import LLMError
from app.llm.queue import LLMJob, LLMTaskQueue
from app.llm.schemas import NamingOutput
from app.llm.settings import get_llm_settings
from app.models.topic import Topic

logger = get_logger("worker.naming")

TASK_TOPIC_ANNOTATION = "topic_annotation"

OUTCOME_NAMED = "named"
OUTCOME_DEGRADED = "degraded"
OUTCOME_SKIPPED = "skipped"

# 恢复探针输入：固定小样本，一次真实命名推理验证推理链路恢复（不伪造成功）
_PROBE_TITLES = ["俄乌双方就停火协议展开新一轮谈判", "多方斡旋推动停火谈判取得进展"]
_PROBE_WORDS = ["停火", "谈判"]


class NamingWorker:
    def __init__(
        self,
        llm_queue: LLMTaskQueue | None = None,
        annotator: TopicAnnotator | None = None,
        session_factory: Any = None,
        redis_client: Any = None,
    ):
        self.settings = get_llm_settings()
        self.llm_queue = llm_queue or LLMTaskQueue(self.settings)
        self.annotator = annotator or TopicAnnotator(settings=self.settings)
        self.session_factory = session_factory or get_session_factory()
        self.redis = redis_client if redis_client is not None else get_cache_redis()
        self._queue_started = False
        self._cooldown: dict[uuid.UUID, float] = {}  # 单点降级议题重试冷却（monotonic 时间戳）

    # ------------------------------------------------------------------
    # 降级恢复探针 + 回填（T2.16 接线的触发方）
    # ------------------------------------------------------------------
    def _attempt_recovery(self) -> bool:
        """降级期恢复探针：模型加载 + 一次真实命名推理均成功才判恢复。

        恢复后立刻对降级期议题（naming_method=ctfidf_fallback）回填重命名/分类/摘要。
        探针失败保持降级状态，下一轮再探（间隔 naming_worker_poll_seconds）。
        """
        engine = self.annotator.engine
        try:
            if not engine.is_loaded:
                engine.load()
            template = prompts.get_prompt(prompts.TASK_NAMING)
            engine.generate_structured(
                template.system,
                template.build_user({"titles": _PROBE_TITLES, "top_words": _PROBE_WORDS}),
                NamingOutput,
                max_retries=0,  # 探针只打一次，不做格式重试
            )
        except LLMError as exc:
            logger.warning("llm_recovery_probe_fail", error=str(exc)[:200])
            return False
        self.annotator.monitor.mark_recovered()
        db = self.session_factory()
        try:
            backfilled = self.annotator.backfill_degraded_topics(db)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        logger.info("llm_recovered_backfill", backfilled=backfilled)
        return True

    # ------------------------------------------------------------------
    # 单议题标注 + 回填（LLMTaskQueue handler 的工作单元，独立线程执行）
    # ------------------------------------------------------------------
    def _annotate_and_persist(self, payload: dict[str, Any]) -> str:
        """标注一个议题并回填；返回 named/degraded/skipped。

        每议题独立事务：单议题失败不影响同批其他议题，也不阻塞主链路。
        """
        topic_id = uuid.UUID(payload["topic_id"])
        annotation = self.annotator.annotate_topic(payload["titles"], payload["top_words"])
        db = self.session_factory()
        try:
            topic = db.get(Topic, topic_id)
            if topic is None or topic.merged_into is not None:
                return OUTCOME_SKIPPED  # 提交后议题被重聚类校正合并/清理：跳过不报错
            self.annotator.record_judgements(db, topic_id, annotation)
            if annotation.degraded:
                # 单点降级不静默：写 P1 告警（防抖）；name/name_auto 落「关键词：」兜底标签
                # 不伪装 LLM 命名；naming_method 保持 ctfidf_fallback，恢复后 backfill 重命名
                write_llm_degraded_alert(
                    db,
                    reason=self.annotator.monitor.reason or annotation.name.error or "llm_service 降级",
                    since=self.annotator.monitor.degraded_since,
                    redis_client=self.redis,
                    debounce_seconds=self.settings.alert_debounce_seconds,
                )
                if "name" not in (topic.human_locked_fields or []):
                    topic.name_auto = str(annotation.name.value)
                    topic.name = str(annotation.name.value)
                db.commit()
                return OUTCOME_DEGRADED
            service = ClusterService(db)
            topic = service.record_llm_naming(
                topic_id,
                name=str(annotation.name.value),
                topic_category=str(annotation.category.value) if annotation.category.success else None,
                summary_zh=str(annotation.summary.value)
                if annotation.summary is not None and annotation.summary.success and annotation.summary.value
                else None,
            )
            topic.llm_model = annotation.name.model_name
            topic.prompt_version = annotation.name.prompt_version
            db.commit()
            logger.info(
                "topic_annotated", topic_id=str(topic_id), name=topic.name,
                category=topic.topic_category, naming_method=topic.naming_method,
            )
            return OUTCOME_NAMED
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    async def _handle_batch(self, jobs: list[LLMJob]) -> None:
        """LLMTaskQueue handler：推理为阻塞 CPU 调用，逐任务放独立线程执行。"""
        for job in jobs:
            if job.future.done():
                continue
            try:
                result = await asyncio.to_thread(self._annotate_and_persist, job.payload)
                job.future.set_result(result)
            except Exception as exc:  # noqa: BLE001 单任务失败兑现异常，不拖垮同批
                logger.error("naming_job_fail", topic_id=job.payload.get("topic_id"), error=str(exc)[:300])
                job.future.set_exception(exc)

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def _list_pending(self) -> list[ClusterDossier]:
        db = self.session_factory()
        try:
            return ClusterService(db).list_pending_naming(limit=self.settings.naming_worker_batch_size)
        finally:
            db.close()

    async def run_once(self) -> int:
        """单轮：降级时先探恢复；否则拉待命名议题 → 投递 LLMTaskQueue → 等回填完成。

        返回本轮以 LLM 结果成功回填的议题数。
        """
        if not self._queue_started:
            await self.llm_queue.start(self._handle_batch)
            self._queue_started = True
        if self.annotator.monitor.degraded and not self._attempt_recovery():
            return 0  # 仍降级：不批量处置（议题本就走兜底且会留痕刷量），下轮再探
        pending = await asyncio.to_thread(self._list_pending)
        now = time.monotonic()
        # 冷却过滤：单点降级议题冷却期内不重复判定（持久失败不至于每轮刷 llm_judgements）
        actionable = [
            d for d in pending
            if now - self._cooldown.get(d.topic_id, 0.0) >= self.settings.naming_worker_retry_cooldown_seconds
        ]
        if not actionable:
            return 0
        set_trace_id(new_trace_id())
        futures = [
            self.llm_queue.submit(TASK_TOPIC_ANNOTATION, {
                "topic_id": str(d.topic_id),
                "titles": list(d.representative_titles),
                "top_words": list(d.keywords),
            })
            for d in actionable
        ]
        results = await asyncio.gather(*futures, return_exceptions=True)
        named = 0
        failed = 0
        for dossier, result in zip(actionable, results, strict=True):
            if isinstance(result, Exception):
                failed += 1
                self._cooldown[dossier.topic_id] = now
            elif result == OUTCOME_NAMED:
                named += 1
                self._cooldown.pop(dossier.topic_id, None)
            elif result == OUTCOME_DEGRADED:
                self._cooldown[dossier.topic_id] = now
        logger.info(
            "naming_round_done", pending=len(pending), submitted=len(actionable),
            named=named, failed=failed,
        )
        return named

    async def run_forever(self) -> None:
        logger.info(
            "naming_worker_start",
            batch_size=self.settings.naming_worker_batch_size,
            poll_seconds=self.settings.naming_worker_poll_seconds,
            profile=self.settings.profile,
        )
        try:
            while True:
                named = await self.run_once()
                if named == 0:
                    await asyncio.sleep(self.settings.naming_worker_poll_seconds)
        finally:
            await self.llm_queue.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="AgendaScope 命名 worker（LLM 议题命名/分类/摘要回填）")
    parser.add_argument("--once", action="store_true", help="单轮处理后退出（巡检/调试用）")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(debug=settings.app_debug)
    worker = NamingWorker()
    if args.once:
        asyncio.run(worker.run_once())
        return
    try:
        asyncio.run(worker.run_forever())
    except KeyboardInterrupt:
        logger.info("naming_worker_stop")


if __name__ == "__main__":
    main()
