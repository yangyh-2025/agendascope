"""采集调度器（T1.13）：自研 asyncio 调度器（替代 celery beat 的取舍见下）。

选型说明：单机私有化部署追求"少进程、易运维"。调度语义简单（按源 crawl_interval 节奏轮询
+ TEMP_FAIL 退避重试 + GDELT 定时），asyncio + ThreadPoolExecutor 即可承载，省去 celery
进程组与 broker 运维面；源配置热更新（T1.13：DB 配置 + 重载信号，保存即生效）通过每 tick
重读 sources 表实现，最长一个 tick（默认 30s）生效，不采用文件 watchdog。

调度语义：
- 每源按 poll_interval（分钟，默认 5min；普通源建议 15min 对齐 IIS 实践）创建 collection_job
- TEMP_FAIL 且 next_run_at 到期的任务优先重跑（退避重试，should_crawl 统一裁决）
- 每轮结束推进源健康状态机并检查源失败率告警
- GDELT 兜底按 gdelt_interval_seconds 独立节奏拉取
"""
import asyncio
import concurrent.futures
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collector.gdelt import GdeltCollector
from app.collector.governance import Governance
from app.collector.pipeline import PipelineCollector
from app.collector.rss_collector import RssCollector
from app.collector.submitter import Submitter
from app.collector.types import FetchError
from app.config import get_settings
from app.core.logging import get_logger, new_trace_id, set_trace_id
from app.db.redis_client import get_cache_redis
from app.db.session import get_session_factory
from app.models.collection import JOB_PENDING, JOB_TEMP_FAIL, CollectionJob
from app.models.source import Source

logger = get_logger("scheduler")


class CollectorScheduler:
    def __init__(self, max_workers: int = 8):
        self.settings = get_settings()
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self._last_gdelt_at: datetime | None = None
        self._stopped = asyncio.Event()

    def stop(self) -> None:
        self._stopped.set()

    async def run_forever(self) -> None:
        logger.info("scheduler_start", tick=self.settings.scheduler_tick_seconds)
        loop = asyncio.get_running_loop()
        while not self._stopped.is_set():
            try:
                await loop.run_in_executor(self.executor, self.tick)
            except Exception as exc:  # noqa: BLE001
                logger.error("scheduler_tick_error", exc_info=exc)
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.settings.scheduler_tick_seconds)
            except asyncio.TimeoutError:
                pass

    # ---------- 同步执行区（线程池内） ----------

    def tick(self) -> None:
        db = get_session_factory()()
        try:
            self._dispatch_retries(db)
            self._dispatch_due_sources(db)
            if self.settings.gdelt_enabled:
                self._dispatch_gdelt(db)
        finally:
            db.close()

    def _dispatch_retries(self, db: Session) -> None:
        now = datetime.now(timezone.utc)
        jobs = db.scalars(
            select(CollectionJob).where(
                CollectionJob.status == JOB_TEMP_FAIL,
                CollectionJob.next_run_at <= now,
                CollectionJob.channel != "gdelt",  # GDELT 由独立周期任务重试，不走源调度路径
            ).limit(50)
        ).all()
        for job in jobs:
            source = db.get(Source, job.source_id)
            if source is None or source.status == "failed":
                continue
            self.executor.submit(self._run_job, source.id, job.id)

    def _dispatch_due_sources(self, db: Session) -> None:
        now = datetime.now(timezone.utc)
        sources = db.scalars(
            select(Source).where(Source.status.in_(("active", "degraded")), Source.collect_mode != "gdelt")
        ).all()
        for source in sources:
            last_scheduled = db.scalar(
                select(func.max(CollectionJob.scheduled_at)).where(CollectionJob.source_id == source.id)
            )
            interval = timedelta(minutes=source.poll_interval_min or 5)
            if last_scheduled and now - last_scheduled < interval:
                continue
            # 上一轮 TEMP_FAIL 未到期时不重复开新轮（退避窗口内）
            pending_retry = db.scalar(
                select(CollectionJob.id).where(
                    CollectionJob.source_id == source.id,
                    CollectionJob.status == JOB_TEMP_FAIL,
                    CollectionJob.next_run_at > now,
                ).limit(1)
            )
            if pending_retry:
                continue
            job = Governance(db, get_cache_redis()).create_job(source.id, source.collect_mode, now)
            db.commit()
            self.executor.submit(self._run_job, source.id, job.id)

    def _dispatch_gdelt(self, db: Session) -> None:
        now = datetime.now(timezone.utc)
        if self._last_gdelt_at and now - self._last_gdelt_at < timedelta(seconds=self.settings.gdelt_interval_seconds):
            return
        self._last_gdelt_at = now
        self.executor.submit(self._run_gdelt)

    def _run_job(self, source_id, job_id) -> None:
        set_trace_id(new_trace_id())
        db = get_session_factory()()
        try:
            redis_client = get_cache_redis()
            gov = Governance(db, redis_client)
            source = db.get(Source, source_id)
            job = db.get(CollectionJob, job_id)
            if source is None or job is None or not gov.job_should_run(job):
                return
            gov.mark_running(job)
            db.commit()

            submitter = Submitter()
            try:
                if source.adapter_type == "pipeline":
                    found, new = PipelineCollector(gov, submitter).run_round(source, job)
                else:
                    found, new = RssCollector(gov, submitter).run_round(source, job)
                gov.mark_success(job, found, new, latency_stats=job.latency_stats)
                gov.update_source_health(source, True)
                logger.info("collect_round_done", source_id=str(source.id), found=found, new=new)
            except FetchError as exc:
                gov.mark_failure(job, str(exc), http_status=exc.http_status)
                gov.update_source_health(source, False, reason=str(exc)[:200])
            except Exception as exc:  # noqa: BLE001
                gov.mark_failure(job, f"采集异常: {exc}")
                gov.update_source_health(source, False, reason=str(exc)[:200])
                logger.error("collect_round_error", source_id=str(source.id), exc_info=exc)
            gov.maybe_alert_source_fail_rate(source)
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.error("run_job_error", source_id=str(source_id), exc_info=exc)
        finally:
            db.close()

    def _run_gdelt(self) -> None:
        set_trace_id(new_trace_id())
        db = get_session_factory()()
        try:
            gov = Governance(db, get_cache_redis())
            from app.services.seed_service import ensure_gdelt_pseudo_source

            pseudo = ensure_gdelt_pseudo_source(db)
            job = gov.create_job(pseudo.id, "gdelt", datetime.now(timezone.utc))
            gov.mark_running(job)
            db.commit()
            try:
                found, new = GdeltCollector(db, gov, Submitter()).run_round(job)
                gov.mark_success(job, found, new)
            except Exception as exc:  # noqa: BLE001
                gov.mark_failure(job, f"GDELT 拉取失败: {exc}")
                logger.error("gdelt_round_error", exc_info=exc)
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.error("gdelt_job_error", exc_info=exc)
        finally:
            db.close()
