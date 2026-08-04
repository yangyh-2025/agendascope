"""合并 worker 进程入口：python -m app.worker.combined_worker（低内存部署专用）。

背景：2G 内存服务器上，7 个独立 Python worker 进程（各 ~50-100MB 常驻 + 峰值）
叠加超过物理内存，系统 swap 打满 → iowait 100% 全系统卡死。

本模块把多个 worker 合并进**单个进程**（线程错峰调度）：
  - snapshot（显著性快照，30min 周期）
  - detection（实体登记 + 事件检测，30min 周期）
  - naming（LLM 议题命名，轮询待命名队列）
  - agenda（归并/消亡/黑名单，60min 周期）
  - nlp（嵌入向量化，消费 raw:articles）——nlp 较频繁且内存峰值大，单独线程

线程错峰：各任务独立周期 + 随机初相位偏移（避免同时触发内存峰值叠加）。
单任务异常不拖垮其他任务（各自 try/except 记日志）。

内存收益：5 个 worker 进程 → 1 个进程（~150MB 常驻，峰值错开），省 ~500MB。
"""
from __future__ import annotations

import argparse
import threading
import time

from app.core.logging import configure_logging, get_logger, new_trace_id, set_trace_id
from app.db.redis_client import get_cache_redis
from app.db.session import get_session_factory

logger = get_logger("worker.combined")


class CombinedWorker:
    """单进程内线程跑多个 worker 循环，各任务独立周期 + 初相位错峰。

    内存峰值防护（2G 服务器关键）：5 个任务并发叠加 > 256M cgroup 上限触发
    OOM 死循环（85 次重启实证）。这里用 Semaphore 限制最多 2 个任务并发：
    NLP（持续消费，常驻 ~80M）+ 任一周期任务（峰值 ~150M）≈ 230M，处于
    256M 上限内的安全水位；其他周期任务互斥串行。
    """

    def __init__(self):
        self.session_factory = get_session_factory()
        self.redis = get_cache_redis()
        self._stop = threading.Event()
        # 周期任务互斥锁：snapshot/detection/naming/agenda 任一时刻只有一个跑
        self._periodic_lock = threading.Lock()

    # ---------- 各任务线程 ----------
    def _loop(self, name: str, interval_s: float, fn) -> None:
        """周期任务线程：首次立即执行（不等初相位），之后每 interval 跑一次。

        首轮立即：detection/snapshot 若延迟 0~interval 随机初相位，用户会看到
        事件/显著性长时间不更新（议程事件地图消失的根因）。

        执行前抢 _periodic_lock：周期任务互斥串行，避免内存峰值叠加触发 OOM。
        """
        set_trace_id(new_trace_id())
        while not self._stop.is_set():
            with self._periodic_lock:
                try:
                    fn()
                except Exception as exc:  # noqa: BLE001 单任务失败不拖垮其他任务
                    logger.error(f"{name}_fail", error=str(exc)[:300])
            time.sleep(interval_s)

    def _snapshot_tick(self) -> None:
        from app.worker.snapshot_worker import SnapshotWorker

        w = SnapshotWorker()
        w.run_once()
        logger.info("combined_snapshot_done")

    def _detection_tick(self) -> None:
        from app.worker.detection_worker import DetectionWorker

        w = DetectionWorker()
        w.run_once()
        logger.info("combined_detection_done")

    def _naming_tick(self) -> None:
        from app.worker.naming_worker import NamingWorker

        # naming 是 async 循环：在线程内起一次性 asyncio run（每轮独立）
        import asyncio

        async def _once():
            w = NamingWorker()
            await w.run_once()

        asyncio.run(_once())
        logger.info("combined_naming_done")

    def _agenda_tick(self) -> None:
        from app.worker.agenda_worker import AgendaWorker

        w = AgendaWorker()
        w.run_once()
        logger.info("combined_agenda_done")

    def _nlp_loop(self) -> None:
        """nlp 是持续消费循环（阻塞 XREAD），单独线程长跑。"""
        from app.worker.nlp_worker import NlpWorker

        w = NlpWorker()
        w.run_forever()

    # ---------- 主入口 ----------
    def run_forever(self) -> None:
        logger.info("combined_worker_start")
        threads = [
            threading.Thread(target=self._loop, args=("snapshot", 1800, self._snapshot_tick), daemon=True),
            threading.Thread(target=self._loop, args=("detection", 900, self._detection_tick), daemon=True),
            threading.Thread(target=self._loop, args=("naming", 300, self._naming_tick), daemon=True),
            threading.Thread(target=self._loop, args=("agenda", 3600, self._agenda_tick), daemon=True),
            threading.Thread(target=self._nlp_loop, daemon=True),
        ]
        for t in threads:
            t.start()
        try:
            while not self._stop.wait(1.0):
                pass
        except KeyboardInterrupt:
            self._stop.set()
        logger.info("combined_worker_stop")


def main() -> None:
    parser = argparse.ArgumentParser(description="AgendaScope 合并 worker（低内存：单进程多任务）")
    args = parser.parse_args()
    settings = None
    try:
        from app.config import get_settings

        settings = get_settings()
    except Exception:  # noqa: BLE001
        pass
    configure_logging(debug=bool(settings and settings.app_debug))
    worker = CombinedWorker()
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        logger.info("combined_worker_stop")


if __name__ == "__main__":
    main()
