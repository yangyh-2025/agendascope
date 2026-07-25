"""T3.14 置信度自动升降 + 修正风暴保护 单元测试（真实 db fixture，禁 Mock）。

覆盖：
  1. watching → suspected 条件全部满足升级
  2. 条件不满足保持 watching
  3. origin_confidence 降 'low' → 撤销回 watching
  4. follower_sequence 清空 → 撤销回 watching
  5. confirmed 不自动降级（人工结论机器不推翻）
  6. 修正风暴：单议题 24h 内 >5 次机器修正 → human_locked_fields 锁定全部 + alerts P1 + 后续 reestimate 不再自动修正
"""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from app.agenda_engine.confidence import (
    STORM_LOCKED_FIELDS,
    check_revision_storm,
    maybe_deescalate,
    maybe_escalate,
)
from app.agenda_engine.revision import append_revision, reestimate_origin
from app.models.agenda import AgendaEvent
from app.models.alert import Alert
from app.models.article import Article
from app.models.topic import Topic, TopicArticle
from tests.conftest import make_source

T0 = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)


def _make_topic(db, **kwargs) -> Topic:
    now = datetime.now(UTC)
    defaults = {
        "name": "confidence 测试议题",
        "name_auto": "confidence 测试议题",
        "naming_method": "ctfidf_fallback",
        "cluster_method": "agglomerative",
        "keywords": ["测试"],
        "country_scope": ["US"],
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
        "id": uuid4(),
        "source_id": source.id,
        "url": f"https://example.com/{uuid4().hex}",
        "url_hash": uuid4().hex.ljust(64, "0")[:64],
        "title": "confidence 测试",
        "language": "en",
        "published_at": T0,
        "country_code": source.country_code,
        "time_source": "feed",
        "is_duplicate": False,
    }
    defaults.update(overrides)
    a = Article(**defaults)
    db.add(a)
    db.flush()
    return a


def _link(db, topic: Topic, article: Article) -> None:
    db.add(TopicArticle(topic_id=topic.id, article_id=article.id, weight=1.0, assign_method="online"))
    db.flush()


def _make_event(db, topic: Topic, **overrides) -> AgendaEvent:
    defaults = {
        "topic_id": topic.id,
        "round_no": 1,
        "status": "watching",
        "confidence": "watching",
        "origin_type": "media",
        "origin_country_code": "GB",
        "origin_source_id": None,
        "origin_entity_id": None,
        "origin_at": T0,
        "origin_confidence": "medium",
        "follower_sequence": [],
        "stats_evidence": None,
        "detection_method": "llm",
        "revision_log": [],
        "human_locked_fields": [],
    }
    defaults.update(overrides)
    event = AgendaEvent(**defaults)
    db.add(event)
    db.flush()
    return event


class TestEscalate:
    def test_watching_to_suspected_all_conditions_met(self, db):
        """全部升级条件满足：watching → suspected。"""
        topic = _make_topic(db)
        event = _make_event(
            db, topic,
            confidence="watching",
            origin_type="media",
            origin_confidence="high",
            follower_sequence=[{"country_code": "CN", "lag_hours": 6.0}],
            detection_method="llm",
        )
        db.commit()

        assert maybe_escalate(db, event) is True
        assert event.confidence == "suspected"

    def test_no_origin_type_no_escalation(self, db):
        """origin_type 未确定：保持 watching。"""
        topic = _make_topic(db)
        event = _make_event(
            db, topic,
            confidence="watching",
            origin_type="media",  # 有效，但测试用 media 也无 follower 时的另一条
            origin_confidence="high",
            follower_sequence=[],  # 无跟随国 → 不升级
        )
        db.commit()

        assert maybe_escalate(db, event) is False
        assert event.confidence == "watching"

    def test_low_origin_confidence_no_escalation(self, db):
        """origin_confidence='low'：不升级。"""
        topic = _make_topic(db)
        event = _make_event(
            db, topic,
            confidence="watching",
            origin_confidence="low",
            follower_sequence=[{"country_code": "CN", "lag_hours": 6.0}],
        )
        db.commit()
        assert maybe_escalate(db, event) is False

    def test_fallback_without_stats_no_escalation(self, db):
        """detection_method='media_time_fallback' 且 stats_evidence 不显著：不升级。"""
        topic = _make_topic(db)
        event = _make_event(
            db, topic,
            confidence="watching",
            origin_confidence="medium",
            follower_sequence=[{"country_code": "CN", "lag_hours": 6.0}],
            detection_method="media_time_fallback",
            stats_evidence=None,
        )
        db.commit()
        assert maybe_escalate(db, event) is False

    def test_fallback_with_significant_stats_escalates(self, db):
        """detection_method='media_time_fallback' 但 stats_evidence 显著：升级。"""
        topic = _make_topic(db)
        event = _make_event(
            db, topic,
            confidence="watching",
            origin_confidence="medium",
            follower_sequence=[{"country_code": "CN", "lag_hours": 6.0}],
            detection_method="media_time_fallback",
            stats_evidence={
                "xcorr": {"significant": True, "p_value": 0.01, "best_lag_days": 1, "max_correlation": 0.6},
            },
        )
        db.commit()
        assert maybe_escalate(db, event) is True
        assert event.confidence == "suspected"

    def test_already_suspected_no_change(self, db):
        """confidence 已是 suspected：maybe_escalate 返回 False 不重复推进。"""
        topic = _make_topic(db)
        event = _make_event(
            db, topic,
            confidence="suspected",
            follower_sequence=[{"country_code": "CN", "lag_hours": 6.0}],
            origin_confidence="high",
        )
        db.commit()
        assert maybe_escalate(db, event) is False
        assert event.confidence == "suspected"


class TestDeescalate:
    def test_origin_confidence_low_drops_to_watching(self, db):
        """origin_confidence 降 'low'：suspected → watching。"""
        topic = _make_topic(db)
        event = _make_event(
            db, topic,
            confidence="suspected",
            origin_confidence="low",
            follower_sequence=[{"country_code": "CN", "lag_hours": 6.0}],
        )
        db.commit()
        assert maybe_deescalate(db, event) is True
        assert event.confidence == "watching"

    def test_follower_cleared_drops_to_watching(self, db):
        """follower_sequence 清空：suspected → watching。"""
        topic = _make_topic(db)
        event = _make_event(
            db, topic,
            confidence="suspected",
            origin_confidence="high",
            follower_sequence=[],  # 清空
        )
        db.commit()
        assert maybe_deescalate(db, event) is True
        assert event.confidence == "watching"

    def test_confirmed_not_deescalated(self, db):
        """confirmed 不自动降级（人工结论机器不推翻）。"""
        topic = _make_topic(db)
        event = _make_event(
            db, topic,
            confidence="confirmed",
            origin_confidence="low",  # 即使 origin_confidence 降 low
            follower_sequence=[],      # follower 也清空
        )
        db.commit()
        assert maybe_deescalate(db, event) is False
        assert event.confidence == "confirmed"

    def test_watching_not_deescalated(self, db):
        """已是 watching：无需再降级。"""
        topic = _make_topic(db)
        event = _make_event(
            db, topic,
            confidence="watching",
            origin_confidence="low",
            follower_sequence=[],
        )
        db.commit()
        assert maybe_deescalate(db, event) is False


class TestRevisionStorm:
    def _build_storm_scenario(self, db, topic, event, machine_count: int) -> None:
        """向 event.revision_log 注入 N 条窗口内的机器修正。"""
        for i in range(machine_count):
            append_revision(
                db, event,
                field="origin_confidence",
                before_value="medium" if i % 2 == 0 else "high",
                after_value="high" if i % 2 == 0 else "medium",
                trigger_evidence={"type": "stats_change", "iteration": i},
                actor="machine",
                model="algorithm/origin_reestimate",
                prompt_version="n/a",
            )
        db.flush()

    def test_below_threshold_not_frozen(self, db, admin_user):
        """窗口内机器修正 ≤5：不冻结。"""
        topic = _make_topic(db)
        event = _make_event(db, topic)
        db.commit()
        self._build_storm_scenario(db, topic, event, machine_count=5)

        assert check_revision_storm(db, event) is False
        assert event.human_locked_fields == []

    def test_over_threshold_frozen_and_alert(self, db, admin_user):
        """窗口内机器修正 >5：human_locked_fields 锁定全部 + alerts P1 写入。"""
        topic = _make_topic(db)
        event = _make_event(db, topic)
        db.commit()
        self._build_storm_scenario(db, topic, event, machine_count=6)

        assert check_revision_storm(db, event) is True
        db.commit()

        db.expire_all()
        event_db = db.get(AgendaEvent, event.id)
        locked = set(event_db.human_locked_fields or [])
        assert set(STORM_LOCKED_FIELDS).issubset(locked)

        # alerts 表写入 P1
        stmt = select(Alert).where(Alert.payload["kind"].astext == "revision_storm")
        alerts = list(db.scalars(stmt).all())
        assert len(alerts) >= 1
        latest = alerts[-1]
        assert latest.payload["priority"] == "P1"
        assert latest.payload["event_id"] == str(event.id)
        assert latest.payload["machine_revision_count"] == 6

    def test_storm_freeze_idempotent(self, db, admin_user):
        """已冻结的事件再次调用 check_revision_storm：幂等返回 True 不重复告警。"""
        topic = _make_topic(db)
        event = _make_event(db, topic)
        db.commit()
        self._build_storm_scenario(db, topic, event, machine_count=6)

        assert check_revision_storm(db, event) is True
        db.commit()

        # 已冻结：再次调用不再写新告警
        stmt = select(Alert).where(Alert.payload["kind"].astext == "revision_storm")
        alerts_before = len(list(db.scalars(stmt).all()))

        assert check_revision_storm(db, event) is True
        db.commit()
        alerts_after = len(list(db.scalars(stmt).all()))
        assert alerts_after == alerts_before

    def test_storm_freeze_blocks_subsequent_reestimate(self, db, admin_user):
        """修正风暴冻结后：后续 reestimate_origin 不再自动修正被锁定字段。"""
        topic = _make_topic(db)
        gb_source = make_source(db, name="GB Media", country_code="GB")
        gb_article = _persist_article(db, gb_source, published_at=T0, country_code="GB")
        _link(db, topic, gb_article)

        event = _make_event(db, topic, origin_country_code="GB", status="watching")
        db.commit()

        # 注入 6 条机器修正（模拟连续风暴）
        self._build_storm_scenario(db, topic, event, machine_count=6)
        # 触发风暴冻结
        assert check_revision_storm(db, event) is True
        db.commit()

        # 新证据：发现一篇更早的 US 报道
        us_source = make_source(db, name="US Media", country_code="US")
        us_article = _persist_article(
            db, us_source, published_at=T0 - timedelta(hours=26), country_code="US",
        )
        _link(db, topic, us_article)
        db.commit()

        # 增量重估
        reestimate_origin(
            db, topic.id,
            trigger={"type": "earlier_article", "article_id": str(us_article.id)},
        )
        db.commit()

        db.expire_all()
        event_db = db.get(AgendaEvent, event.id)
        # 全部 origin_* 字段已被锁定：origin_country_code 不应被自动修正
        assert event_db.origin_country_code == "GB"
        # revision_log 中不应新增 origin_country_code 的机器修正
        new_entries = [
            e for e in event_db.revision_log
            if e["actor"] == "machine" and e["field"] == "origin_country_code"
        ]
        assert len(new_entries) == 0

    def test_out_of_window_revisions_not_counted(self, db, admin_user):
        """超窗（>24h）的机器修正不计入风暴阈值。"""
        topic = _make_topic(db)
        event = _make_event(db, topic)
        db.commit()

        # 注入 6 条 24h 之前的机器修正（手工构造 revised_at 为 48h 前）
        old_ts = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
        for _i in range(6):
            event.revision_log = (event.revision_log or []) + [{
                "seq": len(event.revision_log or []) + 1,
                "revised_at": old_ts,
                "field": "origin_confidence",
                "before_value": "medium",
                "after_value": "high",
                "trigger_evidence": {"type": "stats_change"},
                "actor": "machine",
                "actor_id": None,
                "model": "algorithm/origin_reestimate",
                "prompt_version": "n/a",
                "rejected": False,
            }]
        db.flush()
        db.commit()

        # 全部超窗 → 不冻结
        assert check_revision_storm(db, event) is False
        assert event.human_locked_fields == []
