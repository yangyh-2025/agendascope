"""采集 worker 进程入口：python -m app.collector.worker（或 uvicorn 旁独立进程/容器）。

api_server 与 worker 共用代码库；worker 只跑调度器，不启动 HTTP 服务。
"""
import asyncio

from app.collector.scheduler import CollectorScheduler
from app.config import get_settings
from app.core.logging import configure_logging, get_logger


def main() -> None:
    settings = get_settings()
    configure_logging(debug=settings.app_debug)
    logger = get_logger("worker")
    if not settings.scheduler_enabled:
        logger.info("scheduler_disabled_exit")
        return
    scheduler = CollectorScheduler()
    try:
        asyncio.run(scheduler.run_forever())
    except KeyboardInterrupt:
        scheduler.stop()


if __name__ == "__main__":
    main()
