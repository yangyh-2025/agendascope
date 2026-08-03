"""聚类 worker 进程入口：python -m app.worker.cluster_worker（M2-2 管线接入）。

消费 nlp:embedded（NLP worker 向量化落库后投递，聚类接在向量化之后）：
  在线增量双阈值归簇（T_dup 判重 / T_event 归簇 / 孤证微簇）→ ACK
并按 recluster_interval_minutes 周期触发全局重聚类校正 + 快照发布（首轮启动即校正，
之后每小时一轮；校正失败不阻塞在线归簇，下一轮重试）。

可靠性语义与 NLP worker 一致：XAUTOCLAIM 回收滞留 pending，失败不 ACK 重投递，
单消息尝试超 worker_max_attempts 进死信（nlp:embedded:dlq）；在线归簇幂等，
重投递不重复建簇。
"""
import argparse
import json
import os
import socket
import time
from uuid import UUID

from sqlalchemy import select

from app.clustering import STREAM_EMBEDDED_ARTICLES
from app.clustering.config import get_cluster_settings
from app.clustering.online import OnlineAssigner
from app.clustering.recluster import ReclusterJob
from app.config import get_settings
from app.core.logging import configure_logging, get_logger, new_trace_id, set_trace_id
from app.db.queue import StreamQueue
from app.db.redis_client import get_stream_redis
from app.db.session import get_session_factory
from app.models.article import Article

logger = get_logger("worker.cluster")


class ClusterWorker:
    def __init__(
        self,
        queue: StreamQueue | None = None,
        assigner: OnlineAssigner | None = None,
        recluster_job: ReclusterJob | None = None,
    ):
        settings = get_cluster_settings()
        self.settings = settings
        self.stream = STREAM_EMBEDDED_ARTICLES
        self.group = settings.worker_group
        self.consumer = f"cluster-{socket.gethostname()}-{os.getpid()}"
        self.queue = queue or StreamQueue(get_stream_redis())
        self.session_factory = get_session_factory()
        self.assigner = assigner or OnlineAssigner()
        self.recluster_job = recluster_job or ReclusterJob()
        self._attempts: dict[str, int] = {}
        # 低内存适配：初始为当前时刻，首轮不立即跑全局重聚类（recluster 对大量文章
        # 聚类内存密集，会占住进程饿死在线归簇；延迟到首个周期再校正，在线归簇先跑）
        self._last_recluster = time.monotonic()

    def _reclaim_pending(self) -> list:
        try:
            _, messages, _ = self.queue.client.xautoclaim(
                self.stream, self.group, self.consumer,
                min_idle_time=self.settings.worker_reclaim_idle_ms,
                start_id="0-0", count=self.settings.worker_batch_size,
            )
            return [(str(mid), fields) for mid, fields in messages]
        except Exception as exc:  # noqa: BLE001 回收失败不阻塞新消息消费
            logger.warning("xautoclaim_fail", error=str(exc))
            return []

    def _handle_failure(self, entries: list, exc: Exception) -> None:
        logger.error("cluster_batch_fail", batch=len(entries), error=str(exc)[:300])
        for msg_id, fields in entries:
            attempts = self._attempts.get(msg_id, 0) + 1
            self._attempts[msg_id] = attempts
            if attempts >= self.settings.worker_max_attempts:
                self.queue.to_dlq(self.stream, msg_id, dict(fields), reason=f"attempts_exceeded: {exc}"[:500])
                self.queue.ack(self.stream, self.group, msg_id)
                self._attempts.pop(msg_id, None)
                logger.error("cluster_msg_to_dlq", msg_id=msg_id, attempts=attempts)

    def process_entries(self, entries: list) -> int:
        """在线归簇一批消息，返回成功处理数；失败批按 _handle_failure 处置。"""
        article_ids: list[UUID] = []
        valid: list = []
        for msg_id, fields in entries:
            try:
                data = json.loads(fields.get("data", "{}"))
                article_ids.append(UUID(data["article_id"]))
                valid.append((msg_id, fields))
            except (ValueError, KeyError, TypeError) as exc:
                self.queue.to_dlq(self.stream, msg_id, dict(fields), reason=f"bad_payload: {exc}")
                self.queue.ack(self.stream, self.group, msg_id)
                logger.error("cluster_bad_payload", msg_id=msg_id, error=str(exc))
        if not valid:
            return 0
        set_trace_id(valid[0][1].get("trace_id") or new_trace_id())
        db = self.session_factory()
        try:
            articles = {
                a.id: a
                for a in db.scalars(select(Article).where(Article.id.in_(article_ids))).all()
            }
            for article_id in article_ids:
                article = articles.get(article_id)
                if article is None or article.embedding is None:
                    continue  # 文章被清理或尚未向量化：跳过不阻塞（重聚类窗口会兜底）
                self.assigner.assign(db, article)
            db.commit()
        except Exception as exc:  # noqa: BLE001 统一失败处置：重投递/死信
            db.rollback()
            self._handle_failure(valid, exc)
            return 0
        finally:
            db.close()
        for msg_id, _ in valid:
            self.queue.ack(self.stream, self.group, msg_id)
            self._attempts.pop(msg_id, None)
        return len(valid)

    def maybe_recluster(self) -> bool:
        """到点执行全局重聚类校正 + 快照发布；校正失败记日志下一轮重试，不阻塞在线归簇。

        recluster_interval_minutes=0 时禁用全局重聚类（低内存部署：省重聚类内存峰值）。
        """
        if self.settings.recluster_interval_minutes <= 0:
            return False
        interval_s = self.settings.recluster_interval_minutes * 60
        # _last_recluster=0.0 是"从未执行"哨兵：uptime 短于周期时直接比较会误判未到期
        if self._last_recluster > 0.0 and time.monotonic() - self._last_recluster < interval_s:
            return False
        db = self.session_factory()
        try:
            report = self.recluster_job.run(db, redis_client=self.queue.client)
            self._last_recluster = time.monotonic()
            return not report.skipped
        except Exception as exc:  # noqa: BLE001 校正失败下轮重试
            db.rollback()
            self._last_recluster = time.monotonic()  # 避免每轮空转重试，下一周期再来
            logger.error("recluster_fail", error=str(exc)[:300])
            return False
        finally:
            db.close()

    def run_once(self) -> int:
        """单轮：先校正（到点时），再回收滞留 pending，最后读新消息。"""
        self.maybe_recluster()
        processed = 0
        reclaimed = self._reclaim_pending()
        if reclaimed:
            processed += self.process_entries(reclaimed)
        entries = self.queue.consume(
            self.stream, self.group, self.consumer,
            count=self.settings.worker_batch_size, block_ms=self.settings.worker_block_ms,
        )
        entries = [(str(mid), fields) for mid, fields in entries]
        if entries:
            processed += self.process_entries(entries)
        return processed

    def run_forever(self) -> None:
        logger.info(
            "cluster_worker_start",
            stream=self.stream, group=self.group, consumer=self.consumer,
            batch=self.settings.worker_batch_size,
            recluster_interval_minutes=self.settings.recluster_interval_minutes,
        )
        while True:
            self.run_once()


def main() -> None:
    parser = argparse.ArgumentParser(description="AgendaScope 聚类 worker")
    parser.add_argument("--once", action="store_true", help="单轮处理后退出（巡检/调试用）")
    parser.add_argument("--recluster-once", action="store_true", help="仅执行一轮全局重聚类校正后退出")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(debug=settings.app_debug)
    worker = ClusterWorker()
    if args.recluster_once:
        worker._last_recluster = 0.0
        worker.maybe_recluster()
        return
    if args.once:
        worker.run_once()
        return
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        logger.info("cluster_worker_stop")


if __name__ == "__main__":
    main()
