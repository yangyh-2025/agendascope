"""T3.11 AgendaEvent 状态机与判定单元测试：合法转移、条件评估、事件创建。"""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.agenda_engine.event import (
    EventDetectionInput,
    can_transition_event,
    evaluate_conditions,
    upsert_event,
)
from app.agenda_engine.origin import CountryFollower, MediaOrigin
from app.agenda_engine.stats_evidence import (
    GrangerResult,
    QAPResult,
    StatsEvidence,
    XCorrResult,
)
from app.models.agenda import AgendaEvent
from app.models.topic import Topic


def _make_topic(db, **kwargs) -> Topic:
    defaults = {
        "name": "事件测试议题",
        "name_auto": "事件测试议题",
        "naming_method": "ctfidf_fallback",
        "cluster_method": "agglomerative",
        "keywords": ["事件"],
        "country_scope": ["CN"],
        "lifecycle_state": "forming",
    }
    defaults.update(kwargs)
    topic = Topic(**defaults)
    db.add(topic)
    db.flush()
    return topic


def _make_origin(
    country="CN", confidence="high", hours_ago=5, source_id=None, article_id=None,
) -> MediaOrigin:
    return MediaOrigin(
        article_id=article_id or uuid4(),
        source_id=source_id or uuid4(),
        source_name="新华社",
        country_code=country,
        published_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        is_wire_service=True,
        confidence=confidence,
        needs_review=False,
    )


def _make_follower(country, lag_hours=24) -> CountryFollower:
    return CountryFollower(
        country_code=country,
        first_media_id=uuid4(),
        first_media_name=f"{country} 媒体",
        first_article_id=uuid4(),
        first_published_at=datetime.now(UTC),
        lag_hours=lag_hours,
    )


def _make_stats(significant=True, insufficient=False, sample_size=200) -> StatsEvidence:
    return StatsEvidence(
        article_count=sample_size,
        xcorr=XCorrResult(
            max_correlation=0.65, best_lag_days=2, p_value=0.001,
            significant=significant,
        ) if not insufficient else None,
        granger=GrangerResult(
            f_statistic=15.2, p_value=0.002, best_lag_days=2,
            significant=significant,
        ) if not insufficient else None,
        qap=QAPResult(
            correlation=0.4, p_value=0.01, significant=significant, permutations=1000,
        ) if not insufficient else None,
        insufficient_data=insufficient,
        rejection_reason=None if not insufficient else f"数据量不足（{sample_size}<100）",
    )


class TestEventTransitions:
    @pytest.mark.parametrize(
        "current,target,expected",
        [
            ("watching", "suspected", True),
            ("watching", "dismissed", True),
            ("watching", "archived", True),
            ("watching", "confirmed", False),
            ("suspected", "confirmed", True),
            ("suspected", "revised", True),
            ("suspected", "dismissed", True),
            ("confirmed", "revised", True),
            ("confirmed", "archived", True),
            ("confirmed", "suspected", False),
            ("dismissed", "watching", True),
            ("dismissed", "archived", True),
            ("dismissed", "confirmed", False),
            ("revised", "suspected", True),
            ("revised", "confirmed", True),
            ("archived", "watching", False),
            ("archived", "suspected", False),
        ],
    )
    def test_transitions(self, current, target, expected):
        assert can_transition_event(current, target) is expected


class TestEvaluateConditions:
    def test_all_conditions_met(self, db):
        topic = _make_topic(db)
        input_data = EventDetectionInput(
            topic_id=topic.id,
            media_origin=_make_origin(),
            followers=[_make_follower("US"), _make_follower("GB"), _make_follower("JP")],
            stats=_make_stats(significant=True),
        )
        decision = evaluate_conditions(db, input_data)
        assert decision.should_create is True
        assert all(decision.conditions.values())

    def test_origin_low_confidence_blocks_a(self, db):
        """time_source='crawled' 低置信首发不自动告警：a_origin_clear=False。"""
        topic = _make_topic(db)
        input_data = EventDetectionInput(
            topic_id=topic.id,
            media_origin=_make_origin(confidence="low"),
            followers=[_make_follower("US"), _make_follower("GB"), _make_follower("JP")],
            stats=_make_stats(significant=True),
        )
        decision = evaluate_conditions(db, input_data)
        assert decision.conditions["a_origin_clear"] is False
        assert decision.should_create is False

    def test_person_origin_satisfies_a(self, db):
        """人物首发经 LLM 确认：a_origin_clear=True 无需媒体首发。"""
        topic = _make_topic(db)
        input_data = EventDetectionInput(
            topic_id=topic.id,
            media_origin=None,
            person_origin_entity_id=uuid4(),
            followers=[_make_follower("US"), _make_follower("GB"), _make_follower("JP")],
            stats=_make_stats(significant=True),
        )
        decision = evaluate_conditions(db, input_data)
        assert decision.conditions["a_origin_clear"] is True

    def test_insufficient_followers_blocks_b(self, db):
        """跟随国 < 3：b_followers_enough=False。"""
        topic = _make_topic(db)
        input_data = EventDetectionInput(
            topic_id=topic.id,
            media_origin=_make_origin(),
            followers=[_make_follower("US"), _make_follower("GB")],
            stats=_make_stats(significant=True),
        )
        decision = evaluate_conditions(db, input_data)
        assert decision.conditions["b_followers_enough"] is False
        assert decision.should_create is False

    def test_follower_out_of_window_excluded(self, db):
        """lag_hours 超窗不计入跟随国数。"""
        topic = _make_topic(db)
        input_data = EventDetectionInput(
            topic_id=topic.id,
            media_origin=_make_origin(),
            followers=[
                _make_follower("US", lag_hours=24),
                _make_follower("GB", lag_hours=48),
                _make_follower("JP", lag_hours=24 * 30),  # 30 天 > 14 天窗口
            ],
            stats=_make_stats(significant=True),
        )
        decision = evaluate_conditions(db, input_data)
        # 仅 2 国在窗口内
        assert decision.conditions["b_followers_enough"] is False

    def test_stats_insufficient_data_c_false_but_abd_pass(self, db):
        """样本不足：c_stats_significant=False；a/b/d 满足仍 should_create，并标记显著性待补足。"""
        topic = _make_topic(db)
        input_data = EventDetectionInput(
            topic_id=topic.id,
            media_origin=_make_origin(),
            followers=[_make_follower("US"), _make_follower("GB"), _make_follower("JP")],
            stats=_make_stats(significant=False, insufficient=True, sample_size=50),
        )
        decision = evaluate_conditions(db, input_data)
        assert decision.conditions["c_stats_significant"] is False
        assert decision.should_create is True  # a/b/d 满足 + 样本不足待补足
        assert decision.stats_pending is True

    def test_stats_not_significant_blocks_suspected(self, db):
        """统计已出结论且明确不显著（样本足够、xcorr/granger 均不显著）：不升级 suspected。"""
        topic = _make_topic(db)
        input_data = EventDetectionInput(
            topic_id=topic.id,
            media_origin=_make_origin(),
            followers=[_make_follower("US"), _make_follower("GB"), _make_follower("JP")],
            stats=_make_stats(significant=False, insufficient=False, sample_size=200),
        )
        decision = evaluate_conditions(db, input_data)
        assert decision.conditions["c_stats_significant"] is False
        assert decision.should_create is False
        assert decision.stats_pending is False
        assert "不显著" in decision.reason

    def test_stats_none_treated_as_pending(self, db):
        """stats 为 None（未计算）：按样本不足待补足处理，不阻塞创建。"""
        topic = _make_topic(db)
        input_data = EventDetectionInput(
            topic_id=topic.id,
            media_origin=_make_origin(),
            followers=[_make_follower("US"), _make_follower("GB"), _make_follower("JP")],
            stats=None,
        )
        decision = evaluate_conditions(db, input_data)
        assert decision.should_create is True
        assert decision.stats_pending is True

    def test_topic_nascent_blocks_d(self, db):
        """nascent 孤证微簇不算"新兴"（设计口径仅 forming/confirmed）：d 不满足。"""
        topic = _make_topic(db, lifecycle_state="nascent")
        input_data = EventDetectionInput(
            topic_id=topic.id,
            media_origin=_make_origin(),
            followers=[_make_follower("US"), _make_follower("GB"), _make_follower("JP")],
            stats=_make_stats(significant=True),
        )
        decision = evaluate_conditions(db, input_data)
        assert decision.conditions["d_topic_active"] is False
        assert decision.should_create is False

    def test_topic_archived_blocks_d(self, db):
        topic = _make_topic(db, lifecycle_state="archived")
        input_data = EventDetectionInput(
            topic_id=topic.id,
            media_origin=_make_origin(),
            followers=[_make_follower("US"), _make_follower("GB"), _make_follower("JP")],
            stats=_make_stats(significant=True),
        )
        decision = evaluate_conditions(db, input_data)
        assert decision.conditions["d_topic_active"] is False
        assert decision.should_create is False


class TestUpsertEvent:
    def test_creates_new_event(self, db):
        from tests.conftest import make_source
        topic = _make_topic(db)
        source = make_source(db, country_code="CN", language="zh")
        origin = _make_origin(source_id=source.id)
        input_data = EventDetectionInput(
            topic_id=topic.id,
            media_origin=origin,
            followers=[_make_follower("US"), _make_follower("GB"), _make_follower("JP")],
            stats=_make_stats(),
        )
        decision = evaluate_conditions(db, input_data)
        event = upsert_event(db, input_data, decision)
        assert event is not None
        assert event.status == "suspected"
        assert event.confidence == "suspected"
        assert event.origin_type == "media"
        assert event.origin_country_code == "CN"
        assert event.origin_confidence == "high"
        assert len(event.follower_sequence) == 3
        assert event.stats_evidence["sample_size"] == 200

    def test_stats_pending_marker_written(self, db):
        """样本不足创建的事件：stats_evidence 落 significance_pending=True 标记。"""
        from tests.conftest import make_source
        topic = _make_topic(db)
        source = make_source(db, country_code="CN", language="zh")
        input_data = EventDetectionInput(
            topic_id=topic.id,
            media_origin=_make_origin(source_id=source.id),
            followers=[_make_follower("US"), _make_follower("GB"), _make_follower("JP")],
            stats=_make_stats(significant=False, insufficient=True, sample_size=50),
        )
        decision = evaluate_conditions(db, input_data)
        event = upsert_event(db, input_data, decision)
        assert event is not None
        assert event.stats_evidence["significance_pending"] is True
        assert event.stats_evidence["insufficient_data"] is True

    def test_skip_when_decision_negative(self, db):
        topic = _make_topic(db)
        input_data = EventDetectionInput(
            topic_id=topic.id,
            media_origin=_make_origin(confidence="low"),
            followers=[_make_follower("US")],
            stats=None,
        )
        decision = evaluate_conditions(db, input_data)
        event = upsert_event(db, input_data, decision)
        assert event is None

    def test_no_reset_confirmed_event(self, db):
        """已 confirmed 事件不被自动重置（人工结论机器不推翻）。"""
        topic = _make_topic(db)
        existing = AgendaEvent(
            topic_id=topic.id, round_no=1, status="confirmed", confidence="confirmed",
            origin_type="media", origin_country_code="CN",
            origin_at=datetime.now(UTC) - timedelta(hours=10),
            origin_confidence="high",
            follower_sequence=[], revision_log=[], human_locked_fields=[],
        )
        db.add(existing)
        db.flush()

        input_data = EventDetectionInput(
            topic_id=topic.id,
            media_origin=_make_origin(),
            followers=[_make_follower("US"), _make_follower("GB"), _make_follower("JP")],
            stats=_make_stats(),
        )
        decision = evaluate_conditions(db, input_data)
        event = upsert_event(db, input_data, decision)
        assert event.id == existing.id
        assert event.status == "confirmed"  # 保持原状态

    def test_existing_suspected_returns_existing(self, db):
        """已 suspected 同 round_no 事件不重复创建。"""
        topic = _make_topic(db)
        existing = AgendaEvent(
            topic_id=topic.id, round_no=1, status="suspected", confidence="suspected",
            origin_type="media", origin_country_code="CN",
            origin_at=datetime.now(UTC) - timedelta(hours=10),
            origin_confidence="high",
            follower_sequence=[], revision_log=[], human_locked_fields=[],
        )
        db.add(existing)
        db.flush()

        input_data = EventDetectionInput(
            topic_id=topic.id,
            media_origin=_make_origin(),
            followers=[_make_follower("US"), _make_follower("GB"), _make_follower("JP")],
            stats=_make_stats(),
        )
        decision = evaluate_conditions(db, input_data)
        event = upsert_event(db, input_data, decision)
        assert event.id == existing.id
        # 数据库中该 (topic_id, round_no) 仅一条
        count = db.query(AgendaEvent).filter(
            AgendaEvent.topic_id == topic.id, AgendaEvent.round_no == 1,
        ).count()
        assert count == 1
