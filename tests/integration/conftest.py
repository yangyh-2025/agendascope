"""NLP 集成测试共享夹具：确定性假向量嵌入（1024 维）+ 每测试独立 ES 索引。

云嵌入模式（bge-m3 1024 维）下，集成测试不再依赖本地 mpnet 模型：
mpnet_embedder 返回确定性伪向量生成器（内容哈希 → 1024 维 L2 归一化向量），
保证同一文本向量一致、不同文本可区分，维度与 pgvector 列(1024)对齐。
"""
import math
import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import make_source  # noqa: F401  供本目录测试统一引用

_DIM = 1024  # 与 pgvector 列维度一致（bge-m3）


class _FakeEmbedder:
    """确定性伪向量嵌入：字符 bigram 频率 → 1024 维 L2 归一化向量。

    用字符 bigram 频率（每 bigram crc32 哈希到槽位）使"语义近似"文本（如转载改写、
    同主题多篇报道）产生相近向量，支撑转载判重/归簇机制测试；不同主题 bigram
    分布差异大 → cosine 低。
    注意：假向量只验证"机制"（维度对齐、判重/归簇逻辑、检索链路），不验证
    真实语义/跨语言质量——后者由真实嵌入服务（云 bge-m3）保证，相关测试已标记 skip。
    """

    def embed(self, texts: list[str]) -> list[list[float]]:
        import zlib

        out = []
        for text in texts:
            vec = [0.0] * _DIM
            lowered = text.lower()
            for i in range(len(lowered) - 1):
                idx = zlib.crc32(lowered[i : i + 2].encode("utf-8")) % _DIM
                vec[idx] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out

    def embed_article(self, title: str, summary: str | None, content: str | None) -> list[float]:
        return self.embed([f"{title}\n{summary or ''} {content or ''}"])[0]


@pytest.fixture(scope="session")
def lid_detector():
    from app.nlp.config import get_nlp_settings
    from app.nlp.language import LanguageDetector

    if not get_nlp_settings().lid_model_path.exists():
        pytest.skip("lid.176 模型未下载（models/）")
    return LanguageDetector()


@pytest.fixture(scope="session")
def mpnet_embedder():
    # 云嵌入模式（bge-m3 1024 维）：集成测试统一用确定性假向量，不依赖本地 mpnet 权重
    return _FakeEmbedder()


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
