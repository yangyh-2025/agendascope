"""cluster worker 集成测试：nlp:embedded 消费 → 在线归簇 → ACK；NLP worker 投递衔接。"""
import time

import pytest

from app.clustering import STREAM_EMBEDDED_ARTICLES
from app.clustering.online import OnlineAssigner
from app.clustering.recluster import ReclusterJob
from app.clustering.repository import get_assignment
from app.db.queue import STREAM_RAW_ARTICLES, StreamQueue
from app.worker.cluster_worker import ClusterWorker
from app.worker.nlp_worker import NlpWorker
from tests.conftest import make_source
from tests.integration.conftest import make_article

pytestmark = pytest.mark.integration

TITLE = "The central bank announced a 25 basis point rate cut to support the slowing economy."


@pytest.fixture()
def queue(redis_client):
    for key in (
        STREAM_EMBEDDED_ARTICLES, STREAM_EMBEDDED_ARTICLES + ":dlq",
        STREAM_RAW_ARTICLES, STREAM_RAW_ARTICLES + ":dlq",
    ):
        redis_client.delete(key)
    yield StreamQueue(redis_client)
    for key in list(redis_client.scan_iter(match="cluster:*", count=200)):
        redis_client.delete(key)


def _worker(queue) -> ClusterWorker:
    worker = ClusterWorker(queue=queue, assigner=OnlineAssigner(), recluster_job=ReclusterJob())
    worker._last_recluster = time.monotonic()  # 本测试聚焦在线归簇，不触发整轮校正
    return worker


def test_worker_consumes_and_assigns(db, queue, mpnet_embedder):
    source = make_source(db, language="en")
    article = make_article(db, source, title=TITLE)
    article.embedding = mpnet_embedder.embed([TITLE])[0]
    db.commit()
    queue.publish(STREAM_EMBEDDED_ARTICLES, {"article_id": str(article.id)})

    worker = _worker(queue)
    assert worker.run_once() == 1

    db.expire_all()
    assignment = get_assignment(db, article.id)
    assert assignment is not None and assignment.assign_method == "online"
    assert queue.pending_count(STREAM_EMBEDDED_ARTICLES, worker.group) == 0  # 已 ACK
    assert queue.length(STREAM_EMBEDDED_ARTICLES + ":dlq") == 0


def test_worker_skips_article_without_embedding(db, queue):
    """尚未向量化的消息（乱序/异常投递）：跳过不阻塞，ACK 防积压，重聚类窗口兜底。"""
    source = make_source(db, language="en")
    article = make_article(db, source, title=TITLE)  # 无 embedding
    db.commit()
    queue.publish(STREAM_EMBEDDED_ARTICLES, {"article_id": str(article.id)})

    worker = _worker(queue)
    assert worker.run_once() == 1
    assert get_assignment(db, article.id) is None
    assert queue.pending_count(STREAM_EMBEDDED_ARTICLES, worker.group) == 0


def test_worker_bad_payload_goes_dlq(db, queue):
    queue.publish(STREAM_EMBEDDED_ARTICLES, {"unexpected": "payload"})
    worker = _worker(queue)
    assert worker.run_once() == 0
    assert queue.length(STREAM_EMBEDDED_ARTICLES + ":dlq") == 1
    assert queue.pending_count(STREAM_EMBEDDED_ARTICLES, worker.group) == 0


def test_nlp_worker_publishes_to_embedded_stream(db, queue, es_indexer, lid_detector, mpnet_embedder):
    """衔接验证：NLP worker 向量化落库后投递 nlp:embedded（聚类接在向量化之后）。"""
    source = make_source(db, language="en")
    article = make_article(db, source, title=TITLE)
    db.commit()
    queue.publish(STREAM_RAW_ARTICLES, {"article_id": str(article.id), "source_id": str(source.id)})

    nlp_worker = NlpWorker(queue=queue, detector=lid_detector, embedder=mpnet_embedder, es_indexer=es_indexer)
    assert nlp_worker.run_once() == 1
    assert queue.length(STREAM_EMBEDDED_ARTICLES) == 1

    # 接力：cluster worker 消费同一队列完成归簇
    cluster_worker = _worker(queue)
    assert cluster_worker.run_once() == 1
    db.expire_all()
    assert get_assignment(db, article.id) is not None
