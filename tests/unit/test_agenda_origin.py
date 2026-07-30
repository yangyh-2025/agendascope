"""T3.6 媒体首发锚点 + T3.9 跟随国序列单元测试：真实 db fixture，禁 Mock。

时间字段直接构造（datetime.now(UTC) ± timedelta），不依赖系统时钟 sleep。
所有用例建真实 Source / Article / Topic / TopicArticle 行，调用纯计算函数验证返回结构。
"""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.agenda_engine.origin import (
    compute_follower_sequence,
    detect_media_origin,
)
from app.models.article import Article
from app.models.topic import Topic, TopicArticle
from tests.conftest import make_source

T0 = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)


def _make_topic(db, **kwargs) -> Topic:
    """构造最小可用 Topic（与 test_agenda_lifecycle._make_topic 风格对齐）。"""
    defaults = {
        "name": "测试议题",
        "name_auto": "测试议题",
        "naming_method": "ctfidf_fallback",
        "cluster_method": "agglomerative",
        "keywords": ["测试"],
        "country_scope": ["US"],
        "lifecycle_state": "forming",
    }
    defaults.update(kwargs)
    topic = Topic(**defaults)
    db.add(topic)
    db.flush()
    return topic


def _persist_article(db, source, **overrides) -> Article:
    """真实建 Article 行（不写 embedding，与 origin 模块无关字段保持默认）。"""
    defaults = {
        "id": uuid4(),
        "source_id": source.id,
        "url": f"https://example.com/{uuid4().hex}",
        "url_hash": uuid4().hex.ljust(64, "0")[:64],
        "title": "origin 单元测试",
        "language": "en",
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


class TestDetectMediaOrigin:
    def test_empty_topic_returns_none(self, db):
        """议题下无任何文章：返回 None。"""
        topic = _make_topic(db)
        assert detect_media_origin(db, topic.id) is None

    def test_all_duplicate_returns_none(self, db):
        """议题下全部文章 is_duplicate=True（转载跟风）：返回 None（原创节点已被折叠）。"""
        topic = _make_topic(db)
        source = make_source(db)
        # canonical 指向真实存在的另一篇文章（外键约束）
        canonical = _persist_article(db, source, published_at=T0 - timedelta(hours=1))
        article = _persist_article(db, source, is_duplicate=True, canonical_id=canonical.id)
        _link(db, topic, article)
        assert detect_media_origin(db, topic.id) is None

    def test_single_wire_article_high_confidence(self, db):
        """单文章议题：通讯社原创 → confidence='high'，needs_review=False。"""
        topic = _make_topic(db)
        source = make_source(db, name="Reuters", country_code="GB", media_type="agency")
        article = _persist_article(db, source, published_at=T0, country_code="GB")
        _link(db, topic, article)

        origin = detect_media_origin(db, topic.id)
        assert origin is not None
        assert origin.article_id == article.id
        assert origin.source_id == source.id
        assert origin.source_name == "Reuters"
        assert origin.country_code == "GB"
        assert origin.published_at == T0
        assert origin.is_wire_service is True
        assert origin.confidence == "high"
        assert origin.needs_review is False

    def test_single_regular_media_medium_confidence(self, db):
        """单文章议题：普通媒体原创 → confidence='medium'。"""
        topic = _make_topic(db)
        source = make_source(db, name="Regular Daily", country_code="US", media_type="newspaper")
        article = _persist_article(db, source, published_at=T0, country_code="US")
        _link(db, topic, article)

        origin = detect_media_origin(db, topic.id)
        assert origin is not None
        assert origin.is_wire_service is False
        assert origin.confidence == "medium"
        assert origin.needs_review is False

    def test_crawled_time_source_low_confidence(self, db):
        """time_source='crawled'：发布时间为抓取时间 → confidence='low'，needs_review=True。

        即使来源是通讯社也不提升置信度（时间锚点本身不可信）。
        """
        topic = _make_topic(db)
        source = make_source(db, name="Reuters", country_code="GB", media_type="agency")
        article = _persist_article(
            db, source, published_at=T0, country_code="GB", time_source="crawled",
        )
        _link(db, topic, article)

        origin = detect_media_origin(db, topic.id)
        assert origin is not None
        assert origin.is_wire_service is True  # 来源仍是通讯社
        assert origin.confidence == "low"     # 但时间置信度低
        assert origin.needs_review is True

    def test_earliest_published_wins(self, db):
        """多文章议题：最早 published_at 胜出，与输入顺序无关。"""
        topic = _make_topic(db)
        source_early = make_source(db, name="Early Media", country_code="US")
        source_late = make_source(db, name="Late Media", country_code="US")
        early = _persist_article(db, source_early, published_at=T0, country_code="US")
        late = _persist_article(
            db, source_late, published_at=T0 + timedelta(hours=6), country_code="US",
        )
        # 故意乱序入链
        _link(db, topic, late)
        _link(db, topic, early)

        origin = detect_media_origin(db, topic.id)
        assert origin is not None
        assert origin.article_id == early.id
        assert origin.published_at == T0

    def test_tie_broken_by_wire_service(self, db):
        """同秒并列最早：通讯社胜出（is_wire_service=True 优先）。"""
        topic = _make_topic(db)
        regular = make_source(db, name="Regular Daily", country_code="US", media_type="newspaper")
        wire = make_source(db, name="AFP", country_code="FR", media_type="agency")
        regular_article = _persist_article(db, regular, published_at=T0, country_code="US")
        wire_article = _persist_article(db, wire, published_at=T0, country_code="FR")
        # 故意先链普通媒体，验证通讯社仍胜出
        _link(db, topic, regular_article)
        _link(db, topic, wire_article)

        origin = detect_media_origin(db, topic.id)
        assert origin is not None
        assert origin.article_id == wire_article.id
        assert origin.is_wire_service is True
        assert origin.confidence == "high"

    def test_wire_boost_shifts_anchor_within_window(self, db):
        """通讯社锚点向前倾斜 origin_wire_boost_hours（M5 标定后默认 0.5h）参与比较：
        普通媒体 T0、通讯社 T0+20min（倾斜后 T0-10min）→ 通讯社胜出为首发锚点，
        且 origin.published_at 仍是真实发布时间（倾斜不改写留痕时间）。"""
        topic = _make_topic(db)
        regular = make_source(db, name="Regular Daily", country_code="US", media_type="newspaper")
        wire = make_source(db, name="Reuters", country_code="GB", media_type="agency")
        regular_article = _persist_article(db, regular, published_at=T0, country_code="US")
        wire_article = _persist_article(
            db, wire, published_at=T0 + timedelta(minutes=20), country_code="GB",
        )
        _link(db, topic, regular_article)
        _link(db, topic, wire_article)

        origin = detect_media_origin(db, topic.id)
        assert origin is not None
        assert origin.article_id == wire_article.id
        assert origin.is_wire_service is True
        assert origin.published_at == T0 + timedelta(minutes=20)

    def test_wire_boost_not_applied_beyond_window(self, db):
        """通讯社晚于最早报道超过 boost 窗口（T0+7h > 0.5h）：倾斜后仍晚，最早原创保持首发。"""
        topic = _make_topic(db)
        regular = make_source(db, name="Regular Daily", country_code="US", media_type="newspaper")
        wire = make_source(db, name="Reuters", country_code="GB", media_type="agency")
        regular_article = _persist_article(db, regular, published_at=T0, country_code="US")
        wire_article = _persist_article(
            db, wire, published_at=T0 + timedelta(hours=7), country_code="GB",
        )
        _link(db, topic, regular_article)
        _link(db, topic, wire_article)

        origin = detect_media_origin(db, topic.id)
        assert origin is not None
        assert origin.article_id == regular_article.id
        assert origin.is_wire_service is False
        assert origin.confidence == "medium"

    def test_wire_name_case_insensitive_match(self, db):
        """source.name 大小写不敏感匹配名单：'reuters' / 'REUTERS' 均识别为通讯社。"""
        topic = _make_topic(db)
        source = make_source(db, name="reuters", country_code="GB", media_type="online")
        article = _persist_article(db, source, published_at=T0, country_code="GB")
        _link(db, topic, article)

        origin = detect_media_origin(db, topic.id)
        assert origin is not None
        assert origin.is_wire_service is True
        assert origin.confidence == "high"

    def test_wire_by_media_type_only(self, db):
        """source.name 不在名单，但 media_type='agency' → 仍判通讯社。"""
        topic = _make_topic(db)
        source = make_source(db, name="Unlisted Agency", country_code="JP", media_type="agency")
        article = _persist_article(db, source, published_at=T0, country_code="JP")
        _link(db, topic, article)

        origin = detect_media_origin(db, topic.id)
        assert origin is not None
        assert origin.is_wire_service is True
        assert origin.confidence == "high"


class TestComputeFollowerSequence:
    def _make_origin(self, db, country: str = "US", published_at: datetime = T0):
        """构造 origin 国议题首发并返回 (topic, origin)。"""
        topic = _make_topic(db)
        source = make_source(db, name=f"Origin Media {country}", country_code=country)
        article = _persist_article(db, source, published_at=published_at, country_code=country)
        _link(db, topic, article)
        origin = detect_media_origin(db, topic.id)
        assert origin is not None
        return topic, origin

    def test_no_other_countries_empty_sequence(self, db):
        """仅 origin 国报道：跟随序列为空。"""
        topic, origin = self._make_origin(db)
        followers = compute_follower_sequence(db, topic.id, origin)
        assert followers == []

    def test_three_countries_sorted_by_lag(self, db):
        """3 国跟随报道：排除 origin 国，按 lag_hours 升序返回。"""
        topic, origin = self._make_origin(db, country="US", published_at=T0)

        # CN 6h 后跟进
        cn_source = make_source(db, name="CN Media", country_code="CN")
        cn_article = _persist_article(
            db, cn_source, published_at=T0 + timedelta(hours=6), country_code="CN",
        )
        _link(db, topic, cn_article)

        # GB 2h 后跟进（最早跟随者）
        gb_source = make_source(db, name="GB Media", country_code="GB")
        gb_article = _persist_article(
            db, gb_source, published_at=T0 + timedelta(hours=2), country_code="GB",
        )
        _link(db, topic, gb_article)

        # JP 12h 后跟进
        jp_source = make_source(db, name="JP Media", country_code="JP")
        jp_article = _persist_article(
            db, jp_source, published_at=T0 + timedelta(hours=12), country_code="JP",
        )
        _link(db, topic, jp_article)

        followers = compute_follower_sequence(db, topic.id, origin)
        assert [f.country_code for f in followers] == ["GB", "CN", "JP"]
        assert followers[0].lag_hours == pytest.approx(2.0)
        assert followers[1].lag_hours == pytest.approx(6.0)
        assert followers[2].lag_hours == pytest.approx(12.0)
        assert followers[0].first_article_id == gb_article.id
        assert followers[0].first_media_name == "GB Media"

    def test_country_uses_earliest_article_only(self, db):
        """同一国家多篇文章：只取最早一篇作为该国跟随锚点。"""
        topic, origin = self._make_origin(db, country="US", published_at=T0)

        cn_source = make_source(db, name="CN Media", country_code="CN")
        cn_early = _persist_article(
            db, cn_source, published_at=T0 + timedelta(hours=3), country_code="CN",
        )
        cn_late = _persist_article(
            db, cn_source, published_at=T0 + timedelta(hours=9), country_code="CN",
        )
        _link(db, topic, cn_late)
        _link(db, topic, cn_early)

        followers = compute_follower_sequence(db, topic.id, origin)
        assert len(followers) == 1
        assert followers[0].country_code == "CN"
        assert followers[0].first_article_id == cn_early.id
        assert followers[0].lag_hours == pytest.approx(3.0)

    def test_negative_lag_skipped_with_warning(self, db, caplog):
        """follower 早于 origin（lag<0）：跳过且记 warning（数据异常信号）。"""
        topic = _make_topic(db)
        # 先发一篇"晚"US 报道作为名义首发（让 detect 选它当 origin）
        us_source = make_source(db, name="US Media", country_code="US")
        us_late = _persist_article(
            db, us_source, published_at=T0 + timedelta(hours=5), country_code="US",
        )
        _link(db, topic, us_late)

        # CN 报道比 origin 更早（实际应作为 origin，但调用方传入指定 origin 时验证跳过逻辑）
        cn_source = make_source(db, name="CN Media", country_code="CN")
        cn_early = _persist_article(
            db, cn_source, published_at=T0, country_code="CN",
        )
        _link(db, topic, cn_early)

        origin = detect_media_origin(db, topic.id)
        assert origin is not None
        # 实际 detect 会选 cn_early 作为 origin（最早）；手工构造 origin=us_late 来验证跳过
        forced_origin = type(origin)(
            article_id=us_late.id,
            source_id=us_source.id,
            source_name=us_source.name,
            country_code="US",
            published_at=us_late.published_at,
            is_wire_service=False,
            confidence="medium",
            needs_review=False,
        )
        with caplog.at_level("WARNING", logger="agenda_engine.origin"):
            followers = compute_follower_sequence(db, topic.id, forced_origin)
        assert followers == []
        assert any("follower_lag_negative_skipped" in r.message for r in caplog.records)

    def test_out_of_window_excluded(self, db):
        """follower 超窗（lag > follower_window_days*24）：剔除。"""
        topic, origin = self._make_origin(db, country="US", published_at=T0)

        # 20 天后的报道，超出默认 14 天窗口
        cn_source = make_source(db, name="CN Media", country_code="CN")
        cn_late = _persist_article(
            db, cn_source, published_at=T0 + timedelta(days=20), country_code="CN",
        )
        _link(db, topic, cn_late)

        followers = compute_follower_sequence(db, topic.id, origin)
        assert followers == []

        # 显式扩大窗口至 30 天：CN 应被纳入
        followers_wide = compute_follower_sequence(db, topic.id, origin, window_days=30)
        assert len(followers_wide) == 1
        assert followers_wide[0].country_code == "CN"
        assert followers_wide[0].lag_hours == pytest.approx(20.0 * 24.0)

    def test_excludes_origin_country(self, db):
        """origin 国的其他报道不计入跟随序列（跟随序列只统计他国）。"""
        topic, origin = self._make_origin(db, country="US", published_at=T0)

        # 同国（US）另一媒体 2h 后跟进：不计入跟随
        us2_source = make_source(db, name="US Media 2", country_code="US")
        us2_article = _persist_article(
            db, us2_source, published_at=T0 + timedelta(hours=2), country_code="US",
        )
        _link(db, topic, us2_article)

        # 异国（GB）3h 后跟进：计入
        gb_source = make_source(db, name="GB Media", country_code="GB")
        gb_article = _persist_article(
            db, gb_source, published_at=T0 + timedelta(hours=3), country_code="GB",
        )
        _link(db, topic, gb_article)

        followers = compute_follower_sequence(db, topic.id, origin)
        assert len(followers) == 1
        assert followers[0].country_code == "GB"
        assert followers[0].first_article_id == gb_article.id

    def test_duplicate_articles_excluded(self, db):
        """is_duplicate=True 的转载跟风稿不参与跟随序列（已被回声折叠）。"""
        topic, origin = self._make_origin(db, country="US", published_at=T0)

        # CN 只有一篇转载（is_duplicate=True）：CN 不应出现在跟随序列
        cn_source = make_source(db, name="CN Media", country_code="CN")
        cn_dup = _persist_article(
            db, cn_source,
            published_at=T0 + timedelta(hours=3),
            country_code="CN",
            is_duplicate=True,
            canonical_id=origin.article_id,
        )
        _link(db, topic, cn_dup)

        followers = compute_follower_sequence(db, topic.id, origin)
        assert followers == []
