"""NLP worker 集成测试：raw:articles 消费 → 管线处理 → ACK；失败重投递与死信。

队列用 redis db14（测试库），ES 用每测试独立索引，模型真实加载。
"""
import json
import uuid

import pytest
from sqlalchemy import select

from app.db.queue import STREAM_RAW_ARTICLES, StreamQueue
from app.nlp.es_sync import EsArticleIndexer
from app.nlp.latency import pipeline_latency_sample
from app.worker.nlp_worker import NlpWorker
from tests.conftest import make_source
from tests.integration.conftest import make_article

pytestmark = pytest.mark.integration


@pytest.fixture()
def queue(redis_client):
    for key in (STREAM_RAW_ARTICLES, STREAM_RAW_ARTICLES + ":dlq"):
        redis_client.delete(key)
    return StreamQueue(redis_client)


def test_worker_consumes_and_acks(db, queue, es_indexer, lid_detector, mpnet_embedder):
    source = make_source(db, language="en")
    article = make_article(
        db, source,
        title="国务院发布新的能源发展规划",
        content="国务院常务会议昨日审议通过了新的能源发展规划，提出加快清洁能源替代。",
    )
    db.commit()
    queue.publish(STREAM_RAW_ARTICLES, {"article_id": str(article.id), "source_id": str(source.id)})

    worker = NlpWorker(queue=queue, detector=lid_detector, embedder=mpnet_embedder, es_indexer=es_indexer)
    assert worker.run_once() == 1

    db.expire_all()
    assert article.language == "zh"  # lid.176 纠正源默认语言
    assert article.embedding is not None and len(article.embedding) == 1024
    assert db.execute(select(pipeline_latency_sample)).all()  # 延迟埋点落表
    doc = es_indexer.client.get(index=es_indexer.index, id=str(article.id))
    assert doc["found"] is True
    assert queue.pending_count(STREAM_RAW_ARTICLES, worker.group) == 0  # 已 ACK
    assert queue.length(STREAM_RAW_ARTICLES + ":dlq") == 0


def test_worker_bad_payload_goes_dlq(db, queue, es_indexer, lid_detector, mpnet_embedder):
    queue.publish(STREAM_RAW_ARTICLES, {"unexpected": "payload"})
    worker = NlpWorker(queue=queue, detector=lid_detector, embedder=mpnet_embedder, es_indexer=es_indexer)
    assert worker.run_once() == 0
    assert queue.length(STREAM_RAW_ARTICLES + ":dlq") == 1
    assert queue.pending_count(STREAM_RAW_ARTICLES, worker.group) == 0


def test_worker_es_failure_retries_then_dlq(db, queue, lid_detector, mpnet_embedder):
    """ES 不可达：有界重试后整批不 ACK 滞留 pending；尝试超限进死信，不死等。"""
    source = make_source(db, language="en")
    article = make_article(db, source, title="Parliament passes new energy bill", content="The parliament passed the bill.")
    db.commit()
    queue.publish(STREAM_RAW_ARTICLES, {"article_id": str(article.id), "source_id": str(source.id)})

    broken_es = EsArticleIndexer(url="http://127.0.0.1:9999", index=f"test_dead_{uuid.uuid4().hex[:6]}",
                                 max_retries=1, backoff=0.01)
    worker = NlpWorker(queue=queue, detector=lid_detector, embedder=mpnet_embedder, es_indexer=broken_es)

    assert worker.run_once() == 0  # 处理失败不 ACK
    pending = queue.client.xpending(STREAM_RAW_ARTICLES, worker.group)
    assert pending["pending"] == 1

    # 语言/向量已落 PG（ES 故障不阻塞可见性）
    db.expire_all()
    assert article.embedding is not None

    # 尝试次数打满后再失败 → 死信 + ACK，队列不积压
    msg_id, fields = queue.client.xrange(STREAM_RAW_ARTICLES)[0]
    worker._attempts[msg_id] = worker.settings.worker_max_attempts - 1
    assert worker.process_entries([(msg_id, fields)]) == 0
    assert queue.length(STREAM_RAW_ARTICLES + ":dlq") == 1
    assert queue.pending_count(STREAM_RAW_ARTICLES, worker.group) == 0
    dead = queue.client.xrange(STREAM_RAW_ARTICLES + ":dlq")[0][1]
    assert json.loads(dead["data"])["article_id"] == str(article.id)
    assert "attempts_exceeded" in dead["reason"]
