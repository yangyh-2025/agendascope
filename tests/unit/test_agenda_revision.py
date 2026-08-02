"""T3.13+T3.15 revision_log 留痕 / 增量重估 / 人工确认与否决 单元测试（真实 db fixture，禁 Mock）。

覆盖：
  1. append_revision 字段完整性（seq 单调递增 + 必填字段全在）
  2. append_revision before_value == after_value 拒绝（AssertionError）
  3. append_revision 机器修正缺 model/prompt_version 拒绝（AssertionError）
  4. reestimate_origin：origin_country_code 变化 → revision_log 留痕 + status='revised'
  5. reestimate_origin：field ∈ human_locked_fields 跳过不修正
  6. confirm_event 状态推进 watching → suspected → confirmed
  7. reject_revision 回滚到修正前值 + 新 revision 条目 + human_locked_fields 增加
  8. 机器不推翻人工：reject 后再次 reestimate，被锁定字段不再被自动修正
"""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.agenda_engine.confidence import maybe_escalate
from app.agenda_engine.revision import (
    RevisionError,
    append_revision,
    confirm_event,
    reestimate_origin,
    reject_revision,
)
from app.models.agenda import AgendaEvent
from app.models.article import Article
from app.models.topic import Topic, TopicArticle
from tests.conftest import make_source

T0 = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)


def _make_topic(db, **kwargs) -> Topic:
    now = datetime.now(UTC)
    defaults = {
        "name": "revision 测试议题",
        "name_auto": "revision 测试议题",
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
        "title": "revision 测试",
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


class TestAppendRevisionFields:
    def test_revision_entry_field_completeness(self, db):
        """append_revision 写入的字段必须包含所有必填字段，seq 单调递增。"""
        topic = _make_topic(db)
        event = _make_event(db, topic)
        db.commit()

        # 第一次追加（机器）
        entry1 = append_revision(
            db, event,
            field="origin_country_code",
            before_value="GB",
            after_value="US",
            trigger_evidence={"type": "earlier_article", "article_id": str(uuid4())},
            actor="machine",
            model="algorithm/origin_reestimate",
            prompt_version="n/a",
        )
        # 第二次追加（人工）
        entry2 = append_revision(
            db, event,
            field="status",
            before_value="watching",
            after_value="confirmed",
            trigger_evidence={"type": "manual_confirm"},
            actor="human",
            actor_id=uuid4(),
        )
        db.commit()

        # seq 单调递增
        assert entry1["seq"] == 1
        assert entry2["seq"] == 2

        # 必填字段全在
        required_keys = {
            "seq", "revised_at", "field", "before_value", "after_value",
            "trigger_evidence", "actor", "actor_id", "model", "prompt_version", "rejected",
        }
        for e in (entry1, entry2):
            assert required_keys.issubset(e.keys()), f"缺字段：{required_keys - e.keys()}"

        # 值校验
        assert entry1["actor"] == "machine"
        assert entry1["model"] == "algorithm/origin_reestimate"
        assert entry1["prompt_version"] == "n/a"
        assert entry1["rejected"] is False
        assert entry1["actor_id"] is None
        assert entry2["actor"] == "human"
        assert entry2["actor_id"] is not None

        # DB 中的 revision_log 也完整
        db.expire_all()
        event_db = db.get(AgendaEvent, event.id)
        assert len(event_db.revision_log) == 2
        assert event_db.revision_log[0]["seq"] == 1
        assert event_db.revision_log[1]["seq"] == 2

    def test_append_revision_same_value_rejected(self, db):
        """不变量①：before_value == after_value 拒绝（AssertionError）。"""
        topic = _make_topic(db)
        event = _make_event(db, topic)
        db.commit()

        with pytest.raises(AssertionError, match="不变量①"):
            append_revision(
                db, event,
                field="origin_country_code",
                before_value="GB",
                after_value="GB",  # 无变化 → 拒绝
                trigger_evidence={"type": "earlier_article"},
                actor="machine",
                model="algorithm/origin_reestimate",
                prompt_version="n/a",
            )

    def test_append_revision_empty_trigger_evidence_rejected(self, db):
        """不变量②：trigger_evidence 空 dict 拒绝。"""
        topic = _make_topic(db)
        event = _make_event(db, topic)
        db.commit()

        with pytest.raises(AssertionError, match="不变量②"):
            append_revision(
                db, event,
                field="origin_country_code",
                before_value="GB",
                after_value="US",
                trigger_evidence={},  # 空证据 → 拒绝
                actor="machine",
                model="algorithm/origin_reestimate",
                prompt_version="n/a",
            )

    def test_append_revision_machine_missing_model_rejected(self, db):
        """不变量③：机器修正缺 model 拒绝。"""
        topic = _make_topic(db)
        event = _make_event(db, topic)
        db.commit()

        with pytest.raises(AssertionError, match="不变量③"):
            append_revision(
                db, event,
                field="origin_country_code",
                before_value="GB",
                after_value="US",
                trigger_evidence={"type": "earlier_article"},
                actor="machine",
                model=None,  # 缺 model
                prompt_version="n/a",
            )
        with pytest.raises(AssertionError, match="不变量③"):
            append_revision(
                db, event,
                field="origin_country_code",
                before_value="GB",
                after_value="US",
                trigger_evidence={"type": "earlier_article"},
                actor="machine",
                model="algorithm/origin_reestimate",
                prompt_version=None,  # 缺 prompt_version
            )


class TestReestimateOrigin:
    def test_origin_country_change_appends_revision_and_marks_revised(self, db):
        """origin_country_code 变化：revision_log 留痕 + status='revised'。"""
        topic = _make_topic(db)
        # 初建议题时只有 GB 一篇报道
        gb_source = make_source(db, name="GB Media", country_code="GB")
        gb_article = _persist_article(
            db, gb_source, published_at=T0, country_code="GB",
        )
        _link(db, topic, gb_article)

        event = _make_event(db, topic, origin_country_code="GB", status="watching")
        db.commit()

        # 新证据：发现一篇更早的 US 报道
        us_source = make_source(db, name="US Media", country_code="US")
        us_article = _persist_article(
            db, us_source, published_at=T0 - timedelta(hours=26), country_code="US",
        )
        _link(db, topic, us_article)
        db.commit()

        # 触发增量重估
        result = reestimate_origin(
            db, topic.id,
            trigger={"type": "earlier_article", "article_id": str(us_article.id)},
        )
        db.commit()

        assert result is not None
        assert result.id == event.id
        # origin_country_code 已修正为 US
        db.expire_all()
        event_db = db.get(AgendaEvent, event.id)
        assert event_db.origin_country_code == "US"
        # status='revised'
        assert event_db.status == "revised"
        # revision_log 至少含 origin_country_code + origin_at + status 三条机器修正
        fields = [e["field"] for e in event_db.revision_log]
        assert "origin_country_code" in fields
        assert "origin_at" in fields
        assert "status" in fields
        # 修正条目 actor='machine'
        origin_entry = next(e for e in event_db.revision_log if e["field"] == "origin_country_code")
        assert origin_entry["actor"] == "machine"
        assert origin_entry["before_value"] == "GB"
        assert origin_entry["after_value"] == "US"
        assert origin_entry["trigger_evidence"]["type"] == "earlier_article"
        assert origin_entry["model"] is not None
        assert origin_entry["prompt_version"] is not None
        assert origin_entry["rejected"] is False

    def test_locked_field_skipped_by_machine(self, db):
        """field ∈ human_locked_fields：机器修正跳过该字段。"""
        topic = _make_topic(db)
        gb_source = make_source(db, name="GB Media", country_code="GB")
        gb_article = _persist_article(db, gb_source, published_at=T0, country_code="GB")
        _link(db, topic, gb_article)

        # 事件已人工锁定 origin_country_code（先前已否决过机器对该字段的修正）
        event = _make_event(
            db, topic,
            origin_country_code="GB",
            human_locked_fields=["origin_country_code"],
        )
        db.commit()

        # 新证据：发现更早的 US 报道（机器原本应自动改 origin_country_code）
        us_source = make_source(db, name="US Media", country_code="US")
        us_article = _persist_article(
            db, us_source, published_at=T0 - timedelta(hours=26), country_code="US",
        )
        _link(db, topic, us_article)
        db.commit()

        reestimate_origin(
            db, topic.id,
            trigger={"type": "earlier_article", "article_id": str(us_article.id)},
        )
        db.commit()

        db.expire_all()
        event_db = db.get(AgendaEvent, event.id)
        # 锁定字段未被机器修正
        assert event_db.origin_country_code == "GB"
        # revision_log 不含 origin_country_code 的修正条目
        fields = [e["field"] for e in event_db.revision_log]
        assert "origin_country_code" not in fields


class TestConfirmEvent:
    def test_confirm_watching_to_confirmed(self, db, admin_user):
        """watching → confirmed：状态推进 + revision_log 留痕 + confirmed_by/at 写入。"""
        topic = _make_topic(db)
        event = _make_event(db, topic, status="watching", confidence="watching")
        db.commit()

        result = confirm_event(db, event.id, actor_user_id=admin_user.id)
        db.commit()

        assert result.status == "confirmed"
        assert result.confidence == "confirmed"
        assert result.confirmed_by == admin_user.id
        assert result.confirmed_at is not None
        # revision_log 含 status 字段人工条目
        status_entries = [e for e in result.revision_log if e["field"] == "status"]
        assert len(status_entries) == 1
        entry = status_entries[0]
        assert entry["actor"] == "human"
        assert entry["actor_id"] == str(admin_user.id)
        assert entry["before_value"] == "watching"
        assert entry["after_value"] == "confirmed"

    def test_confirm_full_state_progression(self, db, admin_user):
        """watching → suspected → confirmed 完整状态推进。"""
        topic = _make_topic(db)
        event = _make_event(
            db, topic,
            status="watching", confidence="watching",
            follower_sequence=[{"country_code": "CN", "lag_hours": 6.0}],
            origin_confidence="medium",
        )
        db.commit()

        # 先升 confidence 到 suspected（模拟机器路径）
        escalated = maybe_escalate(db, event)
        assert escalated is True
        assert event.confidence == "suspected"
        event.status = "suspected"
        db.commit()

        # 人工确认 → confirmed
        result = confirm_event(db, event.id, actor_user_id=admin_user.id)
        db.commit()
        assert result.status == "confirmed"
        assert result.confidence == "confirmed"

    def test_confirm_already_confirmed_rejected(self, db, admin_user):
        """已 confirmed 不可重复确认 → 4002。"""
        topic = _make_topic(db)
        event = _make_event(db, topic, status="confirmed", confidence="confirmed")
        db.commit()

        with pytest.raises(RevisionError) as exc_info:
            confirm_event(db, event.id, actor_user_id=admin_user.id)
        assert exc_info.value.code == 4002

    def test_confirm_dismissed_rejected(self, db, admin_user):
        """dismissed 状态不可确认 → 4002。"""
        topic = _make_topic(db)
        event = _make_event(db, topic, status="dismissed")
        db.commit()

        with pytest.raises(RevisionError) as exc_info:
            confirm_event(db, event.id, actor_user_id=admin_user.id)
        assert exc_info.value.code == 4002

    def test_confirm_not_found(self, db, admin_user):
        """事件不存在 → 3001。"""
        with pytest.raises(RevisionError) as exc_info:
            confirm_event(db, uuid4(), actor_user_id=admin_user.id)
        assert exc_info.value.code == 3001


class TestRejectRevision:
    def _prepare_machine_revision(self, db, topic) -> tuple[AgendaEvent, int]:
        """构造：reestimate 产生机器修正，返回 (event, seq)。"""
        gb_source = make_source(db, name="GB Media", country_code="GB")
        gb_article = _persist_article(db, gb_source, published_at=T0, country_code="GB")
        _link(db, topic, gb_article)

        event = _make_event(db, topic, origin_country_code="GB", status="watching")
        db.commit()

        us_source = make_source(db, name="US Media", country_code="US")
        us_article = _persist_article(
            db, us_source, published_at=T0 - timedelta(hours=26), country_code="US",
        )
        _link(db, topic, us_article)
        db.commit()

        reestimate_origin(
            db, topic.id,
            trigger={"type": "earlier_article", "article_id": str(us_article.id)},
        )
        db.commit()

        db.expire_all()
        event_db = db.get(AgendaEvent, event.id)
        # 找到 origin_country_code 的 seq
        target = next(
            e for e in event_db.revision_log if e["field"] == "origin_country_code"
        )
        return event_db, target["seq"]

    def test_reject_rolls_back_field_and_locks(self, db, admin_user):
        """人工否决：字段回滚到 before_value + 追加人工 revision + human_locked_fields 增加。"""
        topic = _make_topic(db)
        event, target_seq = self._prepare_machine_revision(db, topic)
        assert event.origin_country_code == "US"  # 机器已修正为 US

        # 人工否决
        result = reject_revision(
            db, event.id, target_seq,
            actor_user_id=admin_user.id,
            reason="更早报道实为转载组误判，维持原首发判定",
        )
        db.commit()

        # 字段回滚
        assert result.origin_country_code == "GB"
        # human_locked_fields 包含 origin_country_code
        assert "origin_country_code" in (result.human_locked_fields or [])

        # revision_log：原条目 rejected=True + 新增人工条目
        db.expire_all()
        event_db = db.get(AgendaEvent, event.id)
        rejected_entry = next(
            e for e in event_db.revision_log if e["seq"] == target_seq
        )
        assert rejected_entry["rejected"] is True
        # 新增的人工条目
        human_entries = [
            e for e in event_db.revision_log
            if e["actor"] == "human" and e["field"] == "origin_country_code"
        ]
        assert len(human_entries) == 1
        human_entry = human_entries[0]
        assert human_entry["before_value"] == "US"  # 修正后值（回滚前）
        assert human_entry["after_value"] == "GB"   # 修正前值（回滚后）
        assert human_entry["actor_id"] == str(admin_user.id)
        assert human_entry["trigger_evidence"]["type"] == "manual_reject"
        assert human_entry["trigger_evidence"]["original_seq"] == target_seq
        assert "更早报道" in human_entry["trigger_evidence"]["reason"]

    def test_reject_then_reestimate_does_not_override(self, db, admin_user):
        """机器不推翻人工：reject 后再次 reestimate，被锁定字段不再被自动修正。"""
        topic = _make_topic(db)
        event, target_seq = self._prepare_machine_revision(db, topic)

        # 人工否决（origin_country_code 锁定为 GB）
        reject_revision(
            db, event.id, target_seq,
            actor_user_id=admin_user.id,
            reason="维持原判定",
        )
        db.commit()

        # 再次触发增量重估（比如又有新证据）
        reestimate_origin(
            db, topic.id,
            trigger={"type": "earlier_article", "article_id": str(uuid4())},
        )
        db.commit()

        db.expire_all()
        event_db = db.get(AgendaEvent, event.id)
        # origin_country_code 仍为 GB（机器未推翻人工锁定）
        assert event_db.origin_country_code == "GB"
        # revision_log 不再出现 origin_country_code 的机器修正
        machine_country_entries = [
            e for e in event_db.revision_log
            if e["actor"] == "machine" and e["field"] == "origin_country_code"
        ]
        # 只有最初那条（已被否决），不应有新增
        assert len(machine_country_entries) == 1
        assert machine_country_entries[0]["rejected"] is True

    def test_reject_human_revision_rejected(self, db, admin_user):
        """人工修正不可再被否决 → 4002。"""
        topic = _make_topic(db)
        event = _make_event(db, topic, status="watching")
        db.commit()
        # 人工确认产生一条 human revision
        confirm_event(db, event.id, actor_user_id=admin_user.id)
        db.commit()

        human_entry = next(e for e in event.revision_log if e["actor"] == "human")
        with pytest.raises(RevisionError) as exc_info:
            reject_revision(
                db, event.id, human_entry["seq"],
                actor_user_id=admin_user.id,
                reason="尝试否决人工修正",
            )
        assert exc_info.value.code == 4002

    def test_reject_already_rejected_rejected(self, db, admin_user):
        """同一 revision 已被否决，不可再次否决 → 4002。"""
        topic = _make_topic(db)
        event, target_seq = self._prepare_machine_revision(db, topic)

        reject_revision(
            db, event.id, target_seq,
            actor_user_id=admin_user.id, reason="首次否决",
        )
        db.commit()

        with pytest.raises(RevisionError) as exc_info:
            reject_revision(
                db, event.id, target_seq,
                actor_user_id=admin_user.id, reason="再次否决",
            )
        assert exc_info.value.code == 4002

    def test_reject_event_not_found(self, db, admin_user):
        """事件不存在 → 3001。"""
        with pytest.raises(RevisionError) as exc_info:
            reject_revision(
                db, uuid4(), 1, actor_user_id=admin_user.id, reason="测试"
            )
        assert exc_info.value.code == 3001

    def test_reject_revision_seq_not_found(self, db, admin_user):
        """revision_seq 不存在 → 3001。"""
        topic = _make_topic(db)
        event = _make_event(db, topic)
        db.commit()
        with pytest.raises(RevisionError) as exc_info:
            reject_revision(
                db, event.id, 999,
                actor_user_id=admin_user.id, reason="测试",
            )
        assert exc_info.value.code == 3001

    def test_reject_reason_required(self, db, admin_user):
        """reason 必填且非空 → 4002。"""
        topic = _make_topic(db)
        event = _make_event(db, topic)
        db.commit()
        with pytest.raises(RevisionError) as exc_info:
            reject_revision(
                db, event.id, 1, actor_user_id=admin_user.id, reason="",
            )
        assert exc_info.value.code == 4002


class _FakeReestimateLLM:
    """重估 LLM 佐证替身：overturns_origin / reasoning 可控，degraded 可开关。"""

    class _Engine:
        model_name = "test-model"
        is_loaded = True

        def __init__(self, parent):
            self._parent = parent

        def generate_structured(self, system, user, output_model):
            from app.llm.schemas import ReestimateConfirmOutput

            return ReestimateConfirmOutput(**self._parent._result), 0.01

    class _Monitor:
        def __init__(self, degraded):
            self.degraded = degraded

        def record(self, *a, **k):
            pass

    def __init__(self, overturns_origin=False, reasoning="测试", degraded=False):
        self._result = {
            "overturns_origin": overturns_origin,
            "reasoning": reasoning,
        }
        self.engine = self._Engine(self)
        self.monitor = self._Monitor(degraded)


class TestReestimateLLMConfirm:
    """T3.13 增强：重估 LLM 佐证——overturns_origin 决定是否推进 origin_at 修正。"""

    def _setup_low_confidence_event(self, db):
        """构造 origin_confidence='low' 的事件 + 一篇更早的新文章（person_origin 语义）。"""
        topic = _make_topic(db)
        src = make_source(db, country_code="GB")
        article_old = _persist_article(db, src, published_at=T0, country_code="GB")
        _link(db, topic, article_old)
        event = _make_event(db, topic, origin_country_code="GB", origin_confidence="low", status="watching")
        # 更早的新证据（LLM 复核对象）
        new_article = _persist_article(
            db, src, published_at=T0 - timedelta(hours=26), country_code="GB",
        )
        _link(db, topic, new_article)
        db.commit()
        return event, new_article

    def test_overturns_true_proceeds_origin_at_revision(self, db):
        """overturns_origin=True → 推进 origin_at 修正，trigger_evidence 含 llm_overturn。"""
        event, new_article = self._setup_low_confidence_event(db)
        llm = _FakeReestimateLLM(overturns_origin=True, reasoning="更早同源报道")
        db.commit()

        result = reestimate_origin(
            db, event.topic_id,
            trigger={"type": "person_origin", "article_id": str(new_article.id)},
            llm_annotator=llm,
        )
        db.commit()

        assert result is not None
        db.expire_all()
        event_db = db.get(AgendaEvent, event.id)
        assert event_db.origin_at == T0 - timedelta(hours=26)
        origin_entry = next(e for e in event_db.revision_log if e["field"] == "origin_at")
        assert origin_entry["trigger_evidence"]["llm_overturn"] is True
        assert origin_entry["trigger_evidence"]["reasoning"] == "更早同源报道"
        # llm_judgements 留痕
        from app.models.llm import LLMJudgement

        rows = db.query(LLMJudgement).filter(LLMJudgement.task_type == "reestimate_confirm").all()
        assert len(rows) == 1
        assert rows[0].success is True
        assert rows[0].output_payload["overturns_origin"] is True

    def test_overturns_false_keeps_origin(self, db):
        """overturns_origin=False → 保持原判定，origin_at 不被机器修正。"""
        event, new_article = self._setup_low_confidence_event(db)
        llm = _FakeReestimateLLM(overturns_origin=False, reasoning="不同事件")
        db.commit()

        result = reestimate_origin(
            db, event.topic_id,
            trigger={"type": "person_origin", "article_id": str(new_article.id)},
            llm_annotator=llm,
        )
        db.commit()

        assert result is not None
        db.expire_all()
        event_db = db.get(AgendaEvent, event.id)
        assert event_db.origin_at == T0
        fields = [e["field"] for e in event_db.revision_log]
        assert "origin_at" not in fields

    def test_no_llm_pure_algorithm_path(self, db):
        """llm_annotator=None → 纯算法路径：更早证据自动推进 origin_at（与现状一致）。"""
        event, new_article = self._setup_low_confidence_event(db)
        db.commit()

        result = reestimate_origin(
            db, event.topic_id,
            trigger={"type": "earlier_article", "article_id": str(new_article.id)},
        )
        db.commit()

        assert result is not None
        db.expire_all()
        event_db = db.get(AgendaEvent, event.id)
        assert event_db.origin_at == T0 - timedelta(hours=26)
        origin_entry = next(e for e in event_db.revision_log if e["field"] == "origin_at")
        assert origin_entry["trigger_evidence"]["type"] == "earlier_article"
        assert "llm_overturn" not in origin_entry["trigger_evidence"]

    def test_degraded_llm_pure_algorithm_path(self, db):
        """LLM 降级 → 纯算法路径（与 None 等价，不阻塞修正）。"""
        event, new_article = self._setup_low_confidence_event(db)
        llm = _FakeReestimateLLM(overturns_origin=False, degraded=True)
        db.commit()

        result = reestimate_origin(
            db, event.topic_id,
            trigger={"type": "person_origin", "article_id": str(new_article.id)},
            llm_annotator=llm,
        )
        db.commit()

        assert result is not None
        db.expire_all()
        event_db = db.get(AgendaEvent, event.id)
        assert event_db.origin_at == T0 - timedelta(hours=26)
        origin_entry = next(e for e in event_db.revision_log if e["field"] == "origin_at")
        assert "llm_overturn" not in origin_entry["trigger_evidence"]


class TestReestimateIfEarlierArticle:
    """T3.13：新文章归入既有议题且早于首发锚点时触发增量重估。"""

    def test_earlier_article_triggers_reestimate(self, db):
        """议题已有事件 origin_at=T0，新文章 T0-3h 归入 → 重估并把 origin_at 修正到 T0-3h。"""
        from app.agenda_engine.revision import reestimate_if_earlier_article

        topic = _make_topic(db)
        source = make_source(db, country_code="GB")
        article_old = _persist_article(db, source, published_at=T0, country_code="GB")
        _link(db, topic, article_old)
        event = _make_event(db, topic, origin_at=T0, status="suspected", confidence="suspected")

        # 新文章更早（模拟归簇完成后触发）
        earlier = _persist_article(db, source, published_at=T0 - timedelta(hours=3), country_code="GB")
        _link(db, topic, earlier)

        result = reestimate_if_earlier_article(db, topic.id, earlier)
        assert result is not None
        db.refresh(event)
        assert event.origin_at == T0 - timedelta(hours=3)
        assert event.status == "revised"
        fields = [e["field"] for e in event.revision_log]
        assert "origin_at" in fields
        entry = next(e for e in event.revision_log if e["field"] == "origin_at")
        assert entry["trigger_evidence"]["type"] == "earlier_article"
        assert entry["actor"] == "machine"

    def test_later_article_no_trigger(self, db):
        """新文章晚于当前锚点：不触发重估（正常路径）。"""
        from app.agenda_engine.revision import reestimate_if_earlier_article

        topic = _make_topic(db)
        source = make_source(db, country_code="GB")
        article_old = _persist_article(db, source, published_at=T0, country_code="GB")
        _link(db, topic, article_old)
        event = _make_event(db, topic, origin_at=T0, status="suspected", confidence="suspected")

        later = _persist_article(db, source, published_at=T0 + timedelta(hours=2), country_code="GB")
        _link(db, topic, later)

        assert reestimate_if_earlier_article(db, topic.id, later) is None
        db.refresh(event)
        assert event.revision_log == []

    def test_no_event_no_trigger(self, db):
        """议题无 AgendaEvent：返回 None（首次检测由 detection 编排器负责）。"""
        from app.agenda_engine.revision import reestimate_if_earlier_article

        topic = _make_topic(db)
        source = make_source(db, country_code="GB")
        earlier = _persist_article(db, source, published_at=T0 - timedelta(hours=3), country_code="GB")
        _link(db, topic, earlier)

        assert reestimate_if_earlier_article(db, topic.id, earlier) is None

    def test_archived_event_no_trigger(self, db):
        """已归档事件不被自动重估（人工已结案）。"""
        from app.agenda_engine.revision import reestimate_if_earlier_article

        topic = _make_topic(db)
        source = make_source(db, country_code="GB")
        article_old = _persist_article(db, source, published_at=T0, country_code="GB")
        _link(db, topic, article_old)
        event = _make_event(db, topic, origin_at=T0, status="archived")

        earlier = _persist_article(db, source, published_at=T0 - timedelta(hours=3), country_code="GB")
        _link(db, topic, earlier)

        assert reestimate_if_earlier_article(db, topic.id, earlier) is None
        db.refresh(event)
        assert event.revision_log == []
