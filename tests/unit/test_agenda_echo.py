"""T3.1 回声消除折叠单元测试：手工构造单位向量控制 cosine 相似度，禁 Mock。

向量构造思路：
  - 基准向量 e1 = [1, 0, ..., 0]
  - 与 e1 余弦为 s 的向量：v = [s, sqrt(1-s^2), 0, ..., 0]
    （已 L2 归一化；与 e1 点积 = s，范数均为 1，cosine = s）
  - 跨维度向量（与 e1 正交）：e3 = [0, 0, 1, 0, ..., 0]，cosine = 0

议题路径（echo_fold_topic）使用真实 db fixture 建 Source/Topic/Article/TopicArticle。
"""
import math
import uuid
from datetime import UTC, datetime, timedelta

from app.agenda_engine.echo import echo_fold_articles, echo_fold_topic
from app.models.article import Article
from app.models.topic import Topic, TopicArticle

DIM = 768


def _unit(dim_index: int) -> list[float]:
    """在第 dim_index 维取 1，其余为 0 的单位向量。"""
    v = [0.0] * DIM
    v[dim_index] = 1.0
    return v


def _vec_with_cosine(target_cosine: float, base_dim: int = 0, orthogonal_dim: int = 1) -> list[float]:
    """构造与 base 单位向量余弦相似度为 target_cosine 的 L2 归一化向量。"""
    v = [0.0] * DIM
    v[base_dim] = target_cosine
    v[orthogonal_dim] = math.sqrt(max(1.0 - target_cosine * target_cosine, 0.0))
    return v


def _article(
    *,
    published_at: datetime,
    embedding: list[float] | None,
    country: str = "US",
    is_duplicate: bool = False,
    canonical_id: uuid.UUID | None = None,
) -> Article:
    """内存态 Article（不依赖 db，直接喂 echo_fold_articles）。"""
    a = Article()
    a.id = uuid.uuid4()
    a.source_id = uuid.uuid4()
    a.url = f"https://example.com/{a.id.hex}"
    a.url_hash = a.id.hex * 2  # 仅内存场景使用，无需满足 CHAR(64)
    a.title = "test"
    a.language = "en"
    a.published_at = published_at
    a.country_code = country
    a.embedding = embedding
    a.is_duplicate = is_duplicate
    a.canonical_id = canonical_id
    return a


T0 = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)


def test_empty_input_returns_empty():
    assert echo_fold_articles([]) == []


def test_single_article_singleton_node():
    a = _article(published_at=T0, embedding=_unit(0))
    nodes = echo_fold_articles([a])
    assert len(nodes) == 1
    node = nodes[0]
    assert node.canonical_article_id == a.id
    assert node.earliest_pub == T0
    assert node.related_docs == []
    assert node.countries == {"US"}


def test_same_day_high_similarity_folds():
    """同日 Δt≤1d 且 sim≥0.65：折叠，fold_rule=same_day。"""
    a = _article(published_at=T0, embedding=_unit(0), country="US")
    b = _article(
        published_at=T0 + timedelta(hours=6),
        embedding=_vec_with_cosine(0.70),
        country="CN",
    )
    nodes = echo_fold_articles([a, b])
    assert len(nodes) == 1
    node = nodes[0]
    assert node.canonical_article_id == a.id
    assert len(node.related_docs) == 1
    doc = node.related_docs[0]
    assert doc.article_id == b.id
    assert doc.fold_rule == "same_day"
    assert abs(doc.similarity - 0.70) < 1e-3
    assert node.countries == {"US", "CN"}


def test_within_3day_below_threshold_does_not_fold():
    """跨天 1d<Δt≤3d 且 0.65≤sim<0.85：不折叠（同日阈值不适用，需满足 0.85）。"""
    a = _article(published_at=T0, embedding=_unit(0))
    b = _article(
        published_at=T0 + timedelta(days=2),
        embedding=_vec_with_cosine(0.75),
    )
    nodes = echo_fold_articles([a, b])
    assert len(nodes) == 2  # 各自成节点
    assert nodes[0].related_docs == []
    assert nodes[1].related_docs == []


def test_within_3day_above_threshold_folds():
    """跨天 1d<Δt≤3d 且 sim≥0.85：折叠，fold_rule=within_3d。"""
    a = _article(published_at=T0, embedding=_unit(0))
    b = _article(
        published_at=T0 + timedelta(days=2),
        embedding=_vec_with_cosine(0.90),
        country="GB",
    )
    nodes = echo_fold_articles([a, b])
    assert len(nodes) == 1
    node = nodes[0]
    assert len(node.related_docs) == 1
    doc = node.related_docs[0]
    assert doc.fold_rule == "within_3d"
    assert doc.similarity >= 0.85
    assert node.countries == {"US", "GB"}


def test_beyond_3day_never_folds():
    """Δt>3d：threshold=None，无论相似度多高都不折叠（防老议题被新报道绑定）。"""
    a = _article(published_at=T0, embedding=_unit(0))
    b = _article(
        published_at=T0 + timedelta(days=5),
        embedding=_unit(0),  # 完全同向，cosine=1.0，仍不应折叠
    )
    nodes = echo_fold_articles([a, b])
    assert len(nodes) == 2
    assert all(len(n.related_docs) == 0 for n in nodes)


def test_already_duplicate_skipped():
    """已 is_duplicate=True 的输入文章跳过不重复折叠（与在线判重分层）。"""
    a = _article(published_at=T0, embedding=_unit(0))
    b = _article(
        published_at=T0 + timedelta(hours=3),
        embedding=_vec_with_cosine(0.99),
        is_duplicate=True,
        canonical_id=uuid.uuid4(),  # 在线判重已指向别的 canonical
    )
    nodes = echo_fold_articles([a, b])
    # b 被跳过：只产生 a 一个节点，且 a 的 related_docs 不应包含 b
    assert len(nodes) == 1
    assert nodes[0].canonical_article_id == a.id
    assert nodes[0].related_docs == []


def test_missing_embedding_skipped():
    """无向量的文章无比对基础，跳过（调用方保障输入已向量化）。"""
    a = _article(published_at=T0, embedding=None)
    assert echo_fold_articles([a]) == []


def test_canonical_is_earliest_regardless_of_input_order():
    """输入乱序也按 published_at 升序：最早者优先成为 canonical。"""
    late = _article(
        published_at=T0 + timedelta(hours=2),
        embedding=_vec_with_cosine(0.80),
    )
    early = _article(published_at=T0, embedding=_unit(0))
    nodes = echo_fold_articles([late, early])  # 乱序输入
    assert len(nodes) == 1
    assert nodes[0].canonical_article_id == early.id
    assert nodes[0].related_docs[0].article_id == late.id


# -------- 议题 ID 路径：echo_fold_topic 真实落库 --------


def _persist_article(db, source, **overrides) -> Article:
    """真实建 Article 行（沿用 conftest.make_source 的 Source 夹具）。"""
    defaults = {
        "id": uuid.uuid4(),
        "source_id": source.id,
        "url": f"https://example.com/{uuid.uuid4().hex}",
        "url_hash": uuid.uuid4().hex,
        "title": "test title",
        "language": "en",
        "published_at": T0,
        "country_code": "US",
        "embedding": _unit(0),
        "is_duplicate": False,
    }
    defaults.update(overrides)
    a = Article(**defaults)
    db.add(a)
    db.flush()
    return a


def test_echo_fold_topic_persists_duplicate_flags(db):
    """议题路径：真实建 Source/Topic/Article/TopicArticle，验证 is_duplicate/canonical_id 写回。"""
    from tests.conftest import make_source

    source = make_source(db)
    topic = Topic(
        name="回声测试议题",
        name_auto="回声测试议题",
        naming_method="ctfidf_fallback",
        keywords=["回声"],
        cluster_method="agglomerative",
        centroid=_unit(0),
        country_scope=["US"],
        lifecycle_state="forming",
    )
    db.add(topic)
    db.flush()

    # 三篇同议题文章：a0 首发（US），a1 同日高相似（CN，应折叠），a2 跨 5 天（不折叠）
    a0 = _persist_article(db, source, published_at=T0, embedding=_unit(0), country_code="US")
    a1 = _persist_article(
        db, source,
        published_at=T0 + timedelta(hours=8),
        embedding=_vec_with_cosine(0.80),
        country_code="CN",
    )
    a2 = _persist_article(
        db, source,
        published_at=T0 + timedelta(days=5),
        embedding=_unit(0),
        country_code="GB",
    )
    for art in (a0, a1, a2):
        db.add(TopicArticle(topic_id=topic.id, article_id=art.id, weight=1.0, assign_method="online"))
    db.flush()

    nodes = echo_fold_topic(db, topic.id, lookback_days=30)
    db.commit()

    # 节点集：a0 与 a2 各成节点；a1 折叠进 a0 节点
    assert len(nodes) == 2
    node_by_canonical = {n.canonical_article_id: n for n in nodes}
    assert a0.id in node_by_canonical
    assert a2.id in node_by_canonical
    head = node_by_canonical[a0.id]
    assert len(head.related_docs) == 1
    assert head.related_docs[0].article_id == a1.id
    assert head.related_docs[0].fold_rule == "same_day"

    # 数据库写回验证：a1 被标记为转载，canonical 指向 a0；a0/a2 保持非转载
    db.expire_all()
    a0_db = db.get(Article, a0.id)
    a1_db = db.get(Article, a1.id)
    a2_db = db.get(Article, a2.id)
    assert a0_db is not None and a0_db.is_duplicate is False
    assert a1_db is not None and a1_db.is_duplicate is True
    assert a1_db.canonical_id == a0.id
    assert a2_db is not None and a2_db.is_duplicate is False
