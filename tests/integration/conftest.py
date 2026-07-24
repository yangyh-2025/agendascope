"""NLP 集成测试共享夹具：真实模型（缺失时跳过）+ 每测试独立 ES 索引。"""
import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import make_source  # noqa: F401  供本目录测试统一引用


@pytest.fixture(scope="session")
def lid_detector():
    from app.nlp.config import get_nlp_settings
    from app.nlp.language import LanguageDetector

    if not get_nlp_settings().lid_model_path.exists():
        pytest.skip("lid.176 模型未下载（models/）")
    return LanguageDetector()


@pytest.fixture(scope="session")
def mpnet_embedder():
    from app.nlp.config import get_nlp_settings
    from app.nlp.embedding import Embedder

    if not get_nlp_settings().embedding_model_path.exists():
        pytest.skip("mpnet 模型未下载（models/sentence-transformers/）")
    return Embedder()


@pytest.fixture()
def es_indexer():
    """每测试独立索引，用完即删；ES 不可达时跳过。"""
    from elasticsearch import Elasticsearch

    from app.config import get_settings
    from app.nlp.es_sync import EsArticleIndexer

    url = get_settings().elasticsearch_url
    try:
        Elasticsearch(url, request_timeout=3).info()
    except Exception:
        pytest.skip("本地 Elasticsearch 不可达（需先 docker compose up -d elasticsearch）")
    indexer = EsArticleIndexer(index=f"test_articles_{uuid.uuid4().hex[:8]}")
    indexer.ensure_index()
    yield indexer
    indexer.client.options(ignore_status=[404]).indices.delete(index=indexer.index)
    indexer.client.close()


def make_article(db, source, **overrides):
    """构造已落库文章（模拟采集中枢 ingest 后的状态：language=源默认语言，visible_at 已设）。"""
    from app.collector.utils import url_hash
    from app.models.article import Article

    now = datetime.now(UTC)
    defaults = {
        "source_id": source.id,
        "url": f"https://example.com/news/{uuid.uuid4().hex[:10]}",
        "title": "placeholder title",
        "language": source.language,
        "published_at": now,
        "visible_at": now,
        "content_status": "full",
        "source_channel": source.collect_mode,
        "country_code": source.country_code,
    }
    defaults.update(overrides)
    defaults["url_hash"] = url_hash(defaults["url"])
    article = Article(**defaults)
    db.add(article)
    db.flush()
    return article
