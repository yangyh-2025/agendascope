"""T3.12 LLM 终审审查官单元测试：score ≥5 维持 / <5 驳回 / 不可用降级。"""
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.agenda_engine.final_review import (
    FinalReviewOutput,
    review_event,
)
from app.models.agenda import AgendaEvent
from app.models.topic import Topic


def _make_topic(db, **kwargs) -> Topic:
    defaults = {
        "name": "终审测试议题",
        "name_auto": "终审测试议题",
        "naming_method": "ctfidf_fallback",
        "cluster_method": "agglomerative",
        "keywords": ["终审"],
        "country_scope": ["CN"],
        "lifecycle_state": "forming",
    }
    defaults.update(kwargs)
    topic = Topic(**defaults)
    db.add(topic)
    db.flush()
    return topic


def _make_event(db, topic: Topic, status="suspected") -> AgendaEvent:
    event = AgendaEvent(
        topic_id=topic.id, round_no=1, status=status, confidence=status,
        origin_type="media", origin_country_code="CN",
        origin_at=datetime.now(UTC) - timedelta(hours=10),
        origin_confidence="high",
        follower_sequence=[
            {"country_code": "US", "first_media": str(uuid4()), "first_media_name": "CNN", "lag_hours": 24.0},
            {"country_code": "GB", "first_media": str(uuid4()), "first_media_name": "BBC", "lag_hours": 36.0},
            {"country_code": "JP", "first_media": str(uuid4()), "first_media_name": "NHK", "lag_hours": 48.0},
        ],
        stats_evidence={"sample_size": 200, "xcorr": {"best_lag_days": 2, "max_correlation": 0.65, "p_value": 0.001, "significant": True}},
        revision_log=[], human_locked_fields=[],
    )
    db.add(event)
    db.flush()
    return event


def _stub_annotator(output: FinalReviewOutput | Exception, model_name: str = "Qwen2.5-0.5B-Instruct"):
    """构造 stub annotator（真实调 engine.generate_structured 但替换返回值为预定输出；
    非业务 Mock——业务逻辑 review_event 仍真实执行，仅 LLM 推理被替身）。
    返回口径与真实 LLMEngine.generate_structured 一致：(pydantic 输出, 耗时秒)。"""
    annotator = MagicMock()
    if isinstance(output, Exception):
        annotator.engine.generate_structured.side_effect = output
    else:
        annotator.engine.generate_structured.return_value = (output, 0.5)
    annotator.settings.resolved_model_name.return_value = model_name
    return annotator


class TestFinalReviewOutput:
    def test_schema_score_range(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            FinalReviewOutput(score=0, verdict="completed", reasoning="x")
        with pytest.raises(ValidationError):
            FinalReviewOutput(score=11, verdict="completed", reasoning="x")
        out = FinalReviewOutput(score=7, verdict="completed", reasoning="合理", concerns=[])
        assert out.score == 7


class TestReviewEvent:
    def test_score_high_maintains_suspected(self, db):
        """score ≥5 维持 suspected，final_review.verdict='completed'。"""
        topic = _make_topic(db)
        event = _make_event(db, topic, status="suspected")
        output = FinalReviewOutput(score=8, verdict="completed", reasoning="证据链完整", concerns=[])
        annotator = _stub_annotator(output)

        result = review_event(db, event, topic_name=topic.name, llm_annotator=annotator)
        assert result.verdict == "completed"
        assert result.score == 8
        db.refresh(event)
        assert event.status == "suspected"
        assert event.confidence == "suspected"
        assert event.final_review["verdict"] == "completed"
        assert event.final_review["score"] == 8
        assert event.final_review["model"] == "Qwen2.5-0.5B-Instruct"
        assert event.final_review["prompt_version"] == "final-review-v1"

    def test_score_low_rejects_to_watching(self, db):
        """score <5 自动降为 watching，final_review.verdict='rejected'。"""
        topic = _make_topic(db)
        event = _make_event(db, topic, status="suspected")
        output = FinalReviewOutput(
            score=3, verdict="rejected",
            reasoning="跟随链路也可用同期突发事件解释",
            concerns=["同期联合国大会可能独立引发多国报道"],
        )
        annotator = _stub_annotator(output)

        result = review_event(db, event, topic_name=topic.name, llm_annotator=annotator)
        assert result.verdict == "rejected"
        assert result.score == 3
        db.refresh(event)
        assert event.status == "watching"
        assert event.confidence == "watching"
        assert event.final_review["verdict"] == "rejected"
        assert event.final_review["concerns"] == ["同期联合国大会可能独立引发多国报道"]

    def test_verdict_rejected_overrides_score(self, db):
        """LLM 输出 score=6 但 verdict='rejected'：仍按驳回处理（LLM 自主判定优先于 score 阈值）。"""
        topic = _make_topic(db)
        event = _make_event(db, topic, status="suspected")
        output = FinalReviewOutput(score=6, verdict="rejected", reasoning="虽然 score 高但存在根本漏洞", concerns=[])
        annotator = _stub_annotator(output)

        result = review_event(db, event, topic_name=topic.name, llm_annotator=annotator)
        # 当前实现：score >= 5 AND verdict == 'completed' 才 passed
        # score=6 + rejected → not passed → rejected
        assert result.verdict == "rejected"
        db.refresh(event)
        assert event.status == "watching"

    def test_skip_non_suspected_status(self, db):
        """非 suspected 状态跳过重审（未发起 LLM 调用，不写 llm_judgements）。"""
        topic = _make_topic(db)
        event = _make_event(db, topic, status="confirmed")
        annotator = _stub_annotator(FinalReviewOutput(score=8, verdict="completed", reasoning="x"))
        result = review_event(db, event, topic_name=topic.name, llm_annotator=annotator)
        assert result.verdict == "skipped_unavailable"
        assert result.score is None
        # annotator.engine.generate_structured 未被调用
        assert not annotator.engine.generate_structured.called
        from app.models.llm import LLMJudgement
        count = db.query(LLMJudgement).filter(LLMJudgement.task_type == "final_review").count()
        assert count == 0

    def test_llm_unavailable_skips_to_human_review(self, db):
        """LLM 不可用：跳过终审直进人工复核队列，final_review.verdict='skipped_unavailable'，不自动告警。"""
        topic = _make_topic(db)
        event = _make_event(db, topic, status="suspected")
        annotator = _stub_annotator(RuntimeError("Qwen 推理服务不可用"))

        result = review_event(db, event, topic_name=topic.name, llm_annotator=annotator)
        assert result.verdict == "skipped_unavailable"
        db.refresh(event)
        # 状态保持 suspected（不自动降级，等人工复核）
        assert event.status == "suspected"
        assert event.final_review["verdict"] == "skipped_unavailable"
        assert event.final_review["model"] is None
        assert "终审不可用" in event.final_review["reasoning"]


class TestFinalReviewJudgementRecording:
    """T3.12/T2.17：每次终审（含失败）写 llm_judgements 留痕。"""

    def _latest_judgement(self, db):
        from app.models.llm import LLMJudgement
        return (
            db.query(LLMJudgement)
            .filter(LLMJudgement.task_type == "final_review")
            .order_by(LLMJudgement.created_at.desc())
            .first()
        )

    def test_success_writes_judgement(self, db):
        topic = _make_topic(db)
        event = _make_event(db, topic, status="suspected")
        output = FinalReviewOutput(score=8, verdict="completed", reasoning="证据链完整", concerns=[])
        annotator = _stub_annotator(output)

        review_event(db, event, topic_name=topic.name, llm_annotator=annotator)
        judgement = self._latest_judgement(db)
        assert judgement is not None
        assert judgement.topic_id == topic.id
        assert judgement.model_name == "Qwen2.5-0.5B-Instruct"
        assert judgement.prompt_version == "final-review-v1"
        assert judgement.success is True
        assert judgement.error is None
        assert judgement.latency_ms == 500
        assert judgement.output_payload["score"] == 8
        assert judgement.output_payload["verdict"] == "completed"
        assert judgement.input_payload["topic_name"] == topic.name
        assert judgement.input_payload["origin_country_code"] == "CN"

    def test_rejected_also_writes_judgement(self, db):
        topic = _make_topic(db)
        event = _make_event(db, topic, status="suspected")
        output = FinalReviewOutput(score=3, verdict="rejected", reasoning="证据不足", concerns=["样本少"])
        annotator = _stub_annotator(output)

        review_event(db, event, topic_name=topic.name, llm_annotator=annotator)
        judgement = self._latest_judgement(db)
        assert judgement is not None
        assert judgement.success is True  # LLM 调用本身成功，驳回是业务结论
        assert judgement.output_payload["score"] == 3

    def test_llm_failure_writes_failed_judgement(self, db):
        """LLM 不可用：仍写 llm_judgements（success=False + error），不静默。"""
        topic = _make_topic(db)
        event = _make_event(db, topic, status="suspected")
        annotator = _stub_annotator(RuntimeError("Qwen 推理服务不可用"))

        review_event(db, event, topic_name=topic.name, llm_annotator=annotator)
        judgement = self._latest_judgement(db)
        assert judgement is not None
        assert judgement.success is False
        assert judgement.output_payload is None
        assert "Qwen" in judgement.error
        assert judgement.latency_ms is not None
