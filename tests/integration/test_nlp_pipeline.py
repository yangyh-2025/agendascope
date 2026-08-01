"""M2-1 管线集成测试：语言识别→向量化落库→相似度检索→ES 同步→延迟埋点。

云嵌入模式（bge-m3 1024 维）：集成测试用确定性假向量 fixture（不依赖本地模型）。
跨语言语义检索测试需真实嵌入服务，见 test_similarity_search_crosslingual 的 skip。
"""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.nlp.latency import channel_stats, latency_bucket, pipeline_latency_sample
from app.nlp.pipeline import NlpPipeline
from app.nlp.similarity import find_similar, find_similar_to_article
from tests.conftest import make_source
from tests.integration.conftest import make_article

pytestmark = pytest.mark.integration

EN_CLIMATE = "The government announced a new climate policy to cut carbon emissions by 2030."
ZH_CLIMATE = "政府宣布新的气候政策，计划到2030年大幅削减碳排放。"
FOOTBALL = "The local football team won the championship after a dramatic final match."


@pytest.fixture()
def articles(db, lid_detector, mpnet_embedder):
    source = make_source(db, language="en")
    past = datetime.now(UTC) - timedelta(minutes=20)
    a_en = make_article(db, source, title=EN_CLIMATE, content=EN_CLIMATE, summary=EN_CLIMATE, published_at=past)
    a_zh = make_article(db, source, title=ZH_CLIMATE, content=ZH_CLIMATE, summary=ZH_CLIMATE, published_at=past)
    a_fb = make_article(db, source, title=FOOTBALL, content=FOOTBALL, summary=FOOTBALL, published_at=past)
    db.commit()
    return source, a_en, a_zh, a_fb


def test_pipeline_end_to_end(db, es_indexer, lid_detector, mpnet_embedder, articles):
    source, a_en, a_zh, a_fb = articles
    pipeline = NlpPipeline(db, lid_detector, mpnet_embedder, es_indexer)
    metrics = pipeline.process([a_en.id, a_zh.id, a_fb.id])

    assert metrics.processed == 3
    db.expire_all()
    # ① 语言识别：中文报道被 lid.176 正确识别并覆盖源默认语言，置信度留痕
    assert a_zh.language == "zh"
    assert float(a_zh.language_confidence) >= 0.8
    assert a_en.language == "en"
    # ② 向量化落库：1024 维随 articles 落库
    for article in (a_en, a_zh, a_fb):
        assert article.embedding is not None
        assert len(article.embedding) == 1024
    # ③ 延迟埋点：逐篇落表，分桶正确（20min → 15-30m）
    rows = db.execute(select(pipeline_latency_sample)).all()
    assert len(rows) == 3
    for row in rows:
        assert row.latency_bucket == "15-30m"
        assert 19 * 60000 <= row.latency_ms <= 21 * 60000
        assert row.channel == "rss"
    # ④ ES 同步：doc 按 article_id 幂等 upsert，中文进 cjk analyzer 字段
    doc = es_indexer.client.get(index=es_indexer.index, id=str(a_zh.id))
    assert doc["found"] is True
    assert doc["_source"]["language"] == "zh"
    assert doc["_source"]["title_zh"] == ZH_CLIMATE
    assert doc["_source"]["country_code"] == source.country_code


@pytest.mark.skip(reason="假向量 fixture 无跨语言语义；跨语言质量由真实嵌入服务（云 bge-m3）保证，需配置后另测")
def test_similarity_search_crosslingual(db, es_indexer, lid_detector, mpnet_embedder, articles):
    _, a_en, a_zh, a_fb = articles
    NlpPipeline(db, lid_detector, mpnet_embedder, es_indexer).process([a_en.id, a_zh.id, a_fb.id])

    hits = find_similar_to_article(db, a_en.id, top_n=2)
    assert [h.article_id for h in hits] == [a_zh.id, a_fb.id]  # 跨语言同事件报道排第一
    assert hits[0].score > hits[1].score
    assert hits[0].score > 0.5

    filtered = find_similar_to_article(db, a_en.id, top_n=5, min_score=hits[0].score)
    assert [h.article_id for h in filtered] == [a_zh.id]

    db.expire_all()
    direct = find_similar(db, a_en.embedding, top_n=3, exclude_id=a_en.id)
    assert direct[0].article_id == a_zh.id


def test_latency_sample_idempotent_and_stats(db, es_indexer, lid_detector, mpnet_embedder, articles):
    """重投递（Redis Streams 重放）幂等：article_id 唯一，重复处理不产生重复采样。"""
    _, a_en, a_zh, a_fb = articles
    pipeline = NlpPipeline(db, lid_detector, mpnet_embedder, es_indexer)
    pipeline.process([a_en.id, a_zh.id, a_fb.id])
    pipeline.process([a_en.id, a_zh.id, a_fb.id])  # 模拟重投递整批重跑

    count = db.scalar(select(func.count()).select_from(pipeline_latency_sample))
    assert count == 3

    stats = channel_stats(db, datetime.now(UTC) - timedelta(hours=1))
    assert stats == [{"key": "rss", "p95_min": pytest.approx(20.0, abs=1.0), "sample": 3}]


def test_latency_bucket_edges():
    assert latency_bucket(0) == "<5m"
    assert latency_bucket(5 * 60000) == "<5m"
    assert latency_bucket(5 * 60000 + 1) == "5-15m"
    assert latency_bucket(30 * 60000) == "15-30m"
    assert latency_bucket(30 * 60000 + 1) == "30-60m"
    assert latency_bucket(120 * 60000) == "1-2h"
    assert latency_bucket(120 * 60000 + 1) == ">2h"
