"""快照 worker 进程入口：python -m app.worker.snapshot_worker（T3.16）。

按 snapshot_interval_minutes（默认 15 min）周期调 refresh_snapshots：
国家×议题显著性得分/排名 + top_attributes + network_metrics 写 agenda_snapshots 表；
单次计算 > snapshot_timeout_seconds（默认 300s）跳过剩余国家保留上版；
连续 snapshot_failure_alert_threshold（默认 3）次失败写 alerts P1 告警。
"""
import argparse
import time

from app.agenda_engine.config import get_agenda_settings
from app.agenda_engine.snapshot import refresh_snapshots
from app.config import get_settings
from app.core.logging import configure_logging, get_logger, new_trace_id, set_trace_id
from app.db.redis_client import get_cache_redis
from app.db.session import get_session_factory

logger = get_logger("worker.snapshot")


class SnapshotWorker:
    """快照周期任务编排：每 15 min 调 refresh_snapshots。"""

    def __init__(self, session_factory=None, redis_client=None):
        self.settings = get_agenda_settings()
        self.session_factory = session_factory or get_session_factory()
        self.redis = redis_client if redis_client is not None else get_cache_redis()
        self._last_run = 0.0
        self._consecutive_failures_state: dict = {"consecutive_failures": 0}

    def maybe_refresh(self) -> bool:
        """到点触发快照刷新；返回本轮是否实际执行。"""
        interval_s = self.settings.snapshot_interval_minutes * 60
        if time.monotonic() - self._last_run < interval_s:
            return False
        db = self.session_factory()
        try:
            set_trace_id(new_trace_id())
            report = refresh_snapshots(
                db, redis_client=self.redis,
                consecutive_failures_state=self._consecutive_failures_state,
            )
            self._last_run = time.monotonic()
            logger.info(
                "snapshot_refresh_done",
                computed=len(report.computed_countries),
                skipped=len(report.skipped_countries),
                failed=len(report.failed_countries),
                total_topics=report.total_topics,
                elapsed_seconds=report.elapsed_seconds,
                timeout=report.timeout_exceeded,
            )
            return True
        except Exception as exc:  # noqa: BLE001 失败下轮重试
            self._last_run = time.monotonic()
            logger.error("snapshot_refresh_fail", error=str(exc)[:300])
            return False
        finally:
            db.close()

    def run_once(self) -> bool:
        """单轮：到期即触发。"""
        return self.maybe_refresh()

    def run_forever(self) -> None:
        logger.info(
            "snapshot_worker_start",
            interval_minutes=self.settings.snapshot_interval_minutes,
            window_hours=self.settings.snapshot_window_hours,
            timeout_seconds=self.settings.snapshot_timeout_seconds,
        )
        # 启动即首轮触发
        self.maybe_refresh()
        while True:
            time.sleep(self.settings.worker_poll_seconds)
            self.maybe_refresh()


def main() -> None:
    parser = argparse.ArgumentParser(description="AgendaScope 快照 worker（国家×议题显著性 15min 刷新）")
    parser.add_argument("--once", action="store_true", help="单轮触发后退出（巡检/调试用）")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(debug=settings.app_debug)
    worker = SnapshotWorker()
    if args.once:
        worker._last_run = 0.0
        worker.maybe_refresh()
        return
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        logger.info("snapshot_worker_stop")


if __name__ == "__main__":
    main()
