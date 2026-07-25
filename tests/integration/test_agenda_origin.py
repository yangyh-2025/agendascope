"""T3.6+T3.9 媒体首发锚点 + 跟随国序列集成测试：真实 PG，跨语言、跨国家。

覆盖：
  1. 完整链路：3 国 4 媒体跨时报道 → detect_media_origin 锁定通讯社首发 +
     compute_follower_sequence 输出 lag_hours 升序序列
  2. 跨语言同事件（中英报道）：origin 判定与跟随序列与语言无关，仅按时间锚定
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.agenda_engine.origin import (
    compute_follower_sequence,
    detect_media_origin,
)
from app.models.article import Article
from app.models.topic import Topic, TopicArticle
from tests.conftest import make_source

pytestmark = pytest.mark.integration

T0 = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def _make_topic(db, **kwargs) -> Topic:
    now = datetime.now(UTC)
    defaults = {
        "name": "集成测试议题",
        "name_auto": "集成测试议题",
        "naming_method": "ctfidf_fallback",
        "cluster_method": "agglomerative",
        "keywords": ["测试"],
        "country_scope": ["US", "CN", "GB"],
        "lifecycle_state": "forming",
        "first_seen_at": now,
        "last_seen_at": now,
    }
    defaults.update(kwargs)
    topic = Topic(**defaults)
    db.add(topic)
    db.flush()
    return topic


def _persist_article(db, source, **overrides) -> Article:
    defaults = {
        "id": uuid.uuid4(),
        "source_id": source.id,
        "url": f"https://example.com/{uuid.uuid4().hex}",
        "url_hash": uuid.uuid4().hex.ljust(64, "0")[:64],
        "title": "integration origin test",
        "language": source.language,
        "published_at": T0,
        "country_code": source.country_code,
        "time_source": "feed",
        "is_duplicate": False,
    }
    defaults.update(overrides)
    article = Article(**defaults)
    db.add(article)
    db.flush()
    return article


def _link(db, topic: Topic, article: Article) -> None:
    db.add(TopicArticle(topic_id=topic.id, article_id=article.id, weight=1.0, assign_method="online"))
    db.flush()


def test_full_chain_three_countries(db):
    """完整链路：3 国 4 媒体跨时报道 → origin 锁通讯社 + follower 升序。

    时间线（UTC）：
      T0+0h   GB / Reuters / 英文原创（通讯社）       → origin
      T0+2h   US / CNN-like / 英文原创                → follower lag=2
      T0+5h   CN / 新华-like / 中文原创               → follower lag=5
      T0+7h   CN / 澎湃-like / 中文原创（同国第二篇） → 被去重（取 CN 首篇即可）
      T0+9h   JP / 共同社-like / 日文原创             → follower lag=9
    """
    topic = _make_topic(db)

    reuters = make_source(
        db, name="Reuters", country_code="GB", media_type="agency", language="en",
    )
    cnn = make_source(
        db, name="CNN-Like Daily", country_code="US", media_type="online", language="en",
    )
    xinhua_like = make_source(
        db, name="新华风格媒体", country_code="CN", media_type="newspaper", language="zh",
    )
    thepaper = make_source(
        db, name="澎湃风格媒体", country_code="CN", media_type="online", language="zh",
    )
    kyodo = make_source(
        db, name="共同社风格", country_code="JP", media_type="newspaper", language="ja",
    )

    a_reuters = _persist_article(db, reuters, published_at=T0, country_code="GB")
    a_cnn = _persist_article(db, cnn, published_at=T0 + timedelta(hours=2), country_code="US")
    a_xinhua = _persist_article(
        db, xinhua_like, published_at=T0 + timedelta(hours=5), country_code="CN",
    )
    a_thepaper = _persist_article(
        db, thepaper, published_at=T0 + timedelta(hours=7), country_code="CN",
    )
    a_kyodo = _persist_article(
        db, kyodo, published_at=T0 + timedelta(hours=9), country_code="JP",
    )

    for article in (a_reuters, a_cnn, a_xinhua, a_thepaper, a_kyodo):
        _link(db, topic, article)

    origin = detect_media_origin(db, topic.id)
    assert origin is not None
    assert origin.article_id == a_reuters.id
    assert origin.source_id == reuters.id
    assert origin.country_code == "GB"
    assert origin.is_wire_service is True
    assert origin.confidence == "high"
    assert origin.needs_review is False
    assert origin.published_at == T0

    followers = compute_follower_sequence(db, topic.id, origin)
    assert [f.country_code for f in followers] == ["US", "CN", "JP"]
    assert followers[0].lag_hours == pytest.approx(2.0)
    assert followers[1].lag_hours == pytest.approx(5.0)
    assert followers[2].lag_hours == pytest.approx(9.0)
    # CN 取最早一篇（新华风格 5h），不取澎湃风格 7h
    assert followers[1].first_article_id == a_xinhua.id
    assert followers[1].first_media_name == "新华风格媒体"
    # 每个 follower 都关联真实媒体与文章 ID
    for follower in followers:
        assert follower.first_media_id is not None
        assert follower.first_article_id is not None
        assert follower.first_published_at > origin.published_at


def test_cross_language_same_event(db):
    """跨语言同事件：中英报道混合 → origin 与 follower 与语言无关，仅看时间。

    时间线：
      T0+0h   CN / 新华社 / 中文原创（通讯社）  → origin
      T0+3h   US / AP / 英文原创（通讯社）      → follower lag=3
      T0+4h   GB / 普通报纸 / 英文原创          → follower lag=4
    """
    topic = _make_topic(db, country_scope=["CN", "US", "GB"])

    xinhua = make_source(
        db, name="Xinhua", country_code="CN", media_type="agency", language="zh",
    )
    ap = make_source(
        db, name="AP", country_code="US", media_type="agency", language="en",
    )
    guardian_like = make_source(
        db, name="Guardian-Like", country_code="GB", media_type="newspaper", language="en",
    )

    a_xinhua = _persist_article(
        db, xinhua, published_at=T0, country_code="CN", language="zh", title="中文首发",
    )
    a_ap = _persist_article(
        db, ap, published_at=T0 + timedelta(hours=3), country_code="US",
        language="en", title="English follow-up",
    )
    a_gb = _persist_article(
        db, guardian_like, published_at=T0 + timedelta(hours=4), country_code="GB",
        language="en", title="British coverage",
    )

    for article in (a_xinhua, a_ap, a_gb):
        _link(db, topic, article)

    origin = detect_media_origin(db, topic.id)
    assert origin is not None
    # 中文通讯社首发，语言不影响判定
    assert origin.article_id == a_xinhua.id
    assert origin.country_code == "CN"
    assert origin.is_wire_service is True
    assert origin.confidence == "high"

    followers = compute_follower_sequence(db, topic.id, origin)
    assert [f.country_code for f in followers] == ["US", "GB"]
    assert followers[0].first_article_id == a_ap.id
    assert followers[0].lag_hours == pytest.approx(3.0)
    assert followers[1].first_article_id == a_gb.id
    assert followers[1].lag_hours == pytest.approx(4.0)
