"""事件检测编排器单元测试（T3.6-T3.12 主链路，detection.py + detection_worker.py）。

风格与 test_agenda_origin/test_agenda_merge 一致：真实 db fixture 建
Source/Article/Topic/TopicArticle 行，LLM 推理用 stub annotator 替身
（业务链路 detection.detect_topic_event 真实执行，仅 LLM 推理输出被替身，
与 test_agenda_final_review._stub_annotator 同一模式）。

向量构造：每国文章用不同维度的单位向量（cosine=0），避免回声折叠把
跟随报道并入首发节点导致 is_duplicate（折叠后不再计入首发/跟随计算）。
"""
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

from app.agenda_engine.detection import (
    DetectionReport,
    detect_topic_event,
    run_detection_cycle,
)
from app.llm.schemas import FinalReviewOutput, FirstUtteranceOutput
from app.models.agenda import AgendaEvent
from app.models.article import Article
from app.models.llm import LLMJudgement
from app.models.person import PersonOrg
from app.models.topic import Topic, TopicArticle
from tests.conftest import make_source

DIM = 1024


def _unit(dim_index: int) -> list[float]:
    v = [0.0] * DIM
    v[dim_index % DIM] = 1.0
    return v


def _make_topic(db, **kwargs) -> Topic:
    now = datetime.now(UTC)
    defaults = {
        "name": "检测编排测试议题",
        "name_auto": "检测编排测试议题",
        "naming_method": "ctfidf_fallback",
        "cluster_method": "agglomerative",
        "keywords": ["检测"],
        "country_scope": ["CN"],
        "lifecycle_state": "forming",
        "first_seen_at": now - timedelta(hours=12),
        "last_seen_at": now,
    }
    defaults.update(kwargs)
    topic = Topic(**defaults)
    db.add(topic)
    db.flush()
    return topic


def _persist_article(db, source, dim: int, hours_ago: float, **overrides) -> Article:
    defaults = {
        "id": uuid4(),
        "source_id": source.id,
        "url": f"https://example.com/{uuid4().hex}",
        "url_hash": uuid4().hex.ljust(64, "0")[:64],
        "title": "detection 编排测试",
        "language": "en",
        "published_at": datetime.now(UTC) - timedelta(hours=hours_ago),
        "country_code": source.country_code,
        "time_source": "feed",
        "is_duplicate": False,
        "embedding": _unit(dim),
    }
    defaults.update(overrides)
    article = Article(**defaults)
    db.add(article)
    db.flush()
    return article


def _link(db, topic: Topic, article: Article) -> None:
    db.add(TopicArticle(topic_id=topic.id, article_id=article.id, weight=1.0, assign_method="online"))
    db.flush()


def _build_four_country_topic(db, *, origin_wire: bool = False):
    """CN 首发（10h 前）+ US/GB/JP 跟随（8/6/4h 前），均原创未折叠。"""
    topic = _make_topic(db)
    origin_source = make_source(
        db,
        name="Reuters" if origin_wire else "CN Daily",
        country_code="CN",
        media_type="agency" if origin_wire else "newspaper",
    )
    origin = _persist_article(
        db, origin_source, dim=0, hours_ago=10, country_code="CN",
        title="Vladimir Putin announces new energy deal",
        content="Vladimir Putin said the deal covers natural gas exports.",
    )
    followers = []
    for i, (country, hours) in enumerate([("US", 8), ("GB", 6), ("JP", 4)], start=1):
        src = make_source(db, name=f"{country} Media", country_code=country)
        article = _persist_article(db, src, dim=i, hours_ago=hours, country_code=country)
        followers.append(article)
        _link(db, topic, article)
    _link(db, topic, origin)
    return topic, origin, followers


def _stub_annotator(
    *,
    first_utterance_hit: bool = False,
    review_score: int = 8,
    degraded: bool = False,
    engine_error: Exception | None = None,
):
    """LLM 替身：generate_structured 按 output_model 分发到对应 schema 的预定输出。

    返回口径与真实 LLMEngine.generate_structured 一致：(pydantic 输出, 耗时秒)。
    """
    annotator = MagicMock()
    annotator.monitor.degraded = degraded
    annotator.monitor.reason = "推理失败率过高" if degraded else ""
    annotator.monitor.degraded_since = None
    annotator.engine.model_name = "Qwen2.5-0.5B-Instruct"
    annotator.engine.is_loaded = True
    # 首发判定的候选片段预算控制依赖 count_tokens（与 LLMEngine 未加载兜底口径一致：2 字符≈1 token）
    annotator.engine.count_tokens.side_effect = lambda text: max(1, len(text) // 2)
    annotator.settings.resolved_model_name.return_value = "Qwen2.5-0.5B-Instruct"
    calls = {"first_utterance": 0, "final_review": 0}

    def _structured(_system, _user, output_model, max_retries=1):
        if engine_error is not None:
            raise engine_error
        if output_model is FirstUtteranceOutput:
            calls["first_utterance"] += 1
            return (
                FirstUtteranceOutput(
                    is_first_utterance=first_utterance_hit,
                    evidence_quote="Vladimir Putin said the deal covers natural gas exports."
                    if first_utterance_hit else "",
                    confidence="high" if first_utterance_hit else "low",
                    occurred_at="",
                    reasoning="stub 判定",
                ),
                0.1,
            )
        if output_model is FinalReviewOutput:
            calls["final_review"] += 1
            return (
                FinalReviewOutput(
                    score=review_score,
                    verdict="completed" if review_score >= 5 else "rejected",
                    reasoning="stub 终审",
                    concerns=[],
                ),
                0.2,
            )
        raise AssertionError(f"未预期的 output_model: {output_model}")

    annotator.engine.generate_structured.side_effect = _structured
    annotator._calls = calls
    return annotator


class TestDetectTopicEventFullChain:
    def test_full_chain_creates_suspected_event(self, db):
        """完整链路：回声折叠→实体登记→锚点→LLM 首发判定→跟随→统计→事件→终审。"""
        topic, _origin, _followers = _build_four_country_topic(db)
        annotator = _stub_annotator(first_utterance_hit=False, review_score=8)

        result = detect_topic_event(db, topic.id, llm_annotator=annotator)

        assert result.skipped_reason is None
        assert result.echo_nodes == 4  # 向量两两正交，不折叠
        assert result.media_origin_found is True
        assert result.detection_method == "llm"
        assert result.follower_count == 3
        assert result.stats_insufficient is True  # 4 篇 <100 硬性拒绝
        assert result.event_id is not None
        assert result.final_review_verdict == "completed"

        event = db.get(AgendaEvent, result.event_id)
        assert event.status == "suspected"
        assert event.detection_method == "llm"
        assert event.origin_country_code == "CN"
        assert event.origin_confidence == "medium"  # 普通媒体 feed 时间
        assert len(event.follower_sequence) == 3
        # 样本不足：统计显著性待补足标记
        assert event.stats_evidence["insufficient_data"] is True
        assert event.stats_evidence["significance_pending"] is True
        # 终审留痕
        assert event.final_review["verdict"] == "completed"
        assert event.final_review["score"] == 8

    def test_person_origin_hit_sets_person_origin(self, db):
        """LLM 命中人物首发：origin_type='person' + origin_entity_id 落库。"""
        topic, _origin, _followers = _build_four_country_topic(db)
        annotator = _stub_annotator(first_utterance_hit=True, review_score=8)

        result = detect_topic_event(db, topic.id, llm_annotator=annotator)

        assert result.person_origin_entity_id is not None
        event = db.get(AgendaEvent, result.event_id)
        assert event.origin_type == "person"
        assert event.origin_entity_id == result.person_origin_entity_id
        assert event.origin_quote is not None

    def test_entities_registered_into_persons_orgs(self, db):
        """NER 人名提及登记进 persons_orgs（entity_type='person'，monitored=True）。"""
        topic, _origin, _followers = _build_four_country_topic(db)
        annotator = _stub_annotator()

        result = detect_topic_event(db, topic.id, llm_annotator=annotator)

        assert result.entities_registered >= 1
        entity = db.query(PersonOrg).filter(PersonOrg.name == "Vladimir Putin").first()
        assert entity is not None
        assert entity.entity_type == "person"
        assert entity.country_code == "CN"
        assert entity.monitored is True
        # 登记的实体被 match_entities_in_text 命中并进入 LLM 首发判定
        assert result.first_utterance_judges >= 1

    def test_judgements_written_for_llm_calls(self, db):
        """首发判定与终审均写 llm_judgements 留痕（task_type 区分）。"""
        topic, _origin, _followers = _build_four_country_topic(db)
        annotator = _stub_annotator()

        detect_topic_event(db, topic.id, llm_annotator=annotator)

        task_types = {j.task_type for j in db.query(LLMJudgement).all()}
        assert "first_utterance" in task_types
        assert "final_review" in task_types

    def test_no_original_articles_skips(self, db):
        """空议题：无原创报道 → skipped_reason='no_original_articles'，不创建事件。"""
        topic = _make_topic(db)
        result = detect_topic_event(db, topic.id, llm_annotator=_stub_annotator())
        assert result.media_origin_found is False
        assert result.skipped_reason == "no_original_articles"
        assert result.event_id is None

    def test_final_review_not_duplicated_on_second_run(self, db):
        """已有 completed 终审结论的事件：第二轮不重复调 LLM 终审（成本守卫）。"""
        topic, _origin, _followers = _build_four_country_topic(db)
        annotator = _stub_annotator()

        detect_topic_event(db, topic.id, llm_annotator=annotator)
        first_calls = annotator._calls["final_review"]
        assert first_calls == 1

        detect_topic_event(db, topic.id, llm_annotator=annotator)
        assert annotator._calls["final_review"] == first_calls  # 未重复终审


class TestMediaTimeFallback:
    def test_fallback_when_llm_degraded(self, db):
        """LLM 降级：detection_method='media_time_fallback' + 置信度降一级（high→medium），
        终审不可用直进人工复核队列（skipped_unavailable，不自动告警）。"""
        topic, _origin, _followers = _build_four_country_topic(db, origin_wire=True)
        annotator = _stub_annotator(degraded=True, engine_error=RuntimeError("Qwen 推理服务不可用"))

        result = detect_topic_event(db, topic.id, llm_annotator=annotator)

        assert result.detection_method == "media_time_fallback"
        assert result.first_utterance_judges == 0  # 降级不发起首发判定
        event = db.get(AgendaEvent, result.event_id)
        assert event.detection_method == "media_time_fallback"
        # 通讯社原本 high，回落降一级 → medium
        assert event.origin_confidence == "medium"
        # 终审不可用：直进人工复核队列，状态不自动变更
        assert event.final_review["verdict"] == "skipped_unavailable"
        assert event.status == "suspected"
        # 首发判定未写 llm_judgements（未发起）；终审失败留痕 success=False
        fu = db.query(LLMJudgement).filter(LLMJudgement.task_type == "first_utterance").count()
        assert fu == 0
        fr = (
            db.query(LLMJudgement)
            .filter(LLMJudgement.task_type == "final_review")
            .order_by(LLMJudgement.created_at.desc())
            .first()
        )
        assert fr is not None and fr.success is False

    def test_fallback_when_annotator_none(self, db):
        """llm_annotator 未注入：同样走 media_time_fallback 回落。

        用通讯社起源（high→medium 降级后仍满足首发源明确条件）；普通媒体
        medium→low 降级后按设计不创建 suspected 事件（低置信首发不自动告警）。
        """
        topic, _origin, _followers = _build_four_country_topic(db, origin_wire=True)

        result = detect_topic_event(db, topic.id, llm_annotator=None)

        assert result.detection_method == "media_time_fallback"
        event = db.get(AgendaEvent, result.event_id)
        assert event.detection_method == "media_time_fallback"
        assert event.final_review["verdict"] == "skipped_unavailable"


class TestRunDetectionCycle:
    def test_cycle_processes_multiple_topics(self, db):
        """一轮周期：两个活跃议题各自产出事件，报告计数正确。"""
        topic1, _o1, _f1 = _build_four_country_topic(db)
        topic2, _o2, _f2 = _build_four_country_topic(db)
        annotator = _stub_annotator()

        report = run_detection_cycle(
            db, llm_annotator=annotator, topic_ids=[topic1.id, topic2.id],
        )

        assert isinstance(report, DetectionReport)
        assert report.scanned == 2
        assert report.events_created == 2
        assert report.events_reviewed == 2
        assert report.failed_topics == []

    def test_cycle_isolates_single_topic_failure(self, db, monkeypatch):
        """单议题异常：回滚该议题、记入 failed_topics，其他议题正常落库。"""
        topic_ok, _o, _f = _build_four_country_topic(db)
        topic_bad = _make_topic(db, name="故障议题")
        # 周期按议题独立 commit/rollback：议题数据需先落库，否则故障议题触发的
        # rollback 会把同事务内未提交的夹具数据一并回滚（生产上议题本就是已提交数据）
        db.commit()
        annotator = _stub_annotator()

        import app.agenda_engine.detection as detection_mod

        real_detect = detection_mod.detect_topic_event

        def _flaky(db_, topic_id, **kwargs):
            if topic_id == topic_bad.id:
                raise RuntimeError("注入的议题级故障")
            return real_detect(db_, topic_id, **kwargs)

        monkeypatch.setattr(detection_mod, "detect_topic_event", _flaky)

        report = run_detection_cycle(
            db, llm_annotator=annotator, topic_ids=[topic_bad.id, topic_ok.id],
        )

        assert report.failed_topics == [topic_bad.id]
        assert report.events_created == 1
        # 正常议题的事件已独立 commit 落库，不受故障议题回滚影响
        event = db.query(AgendaEvent).filter(AgendaEvent.topic_id == topic_ok.id).first()
        assert event is not None

    def test_cycle_writes_degraded_alert(self, db):
        """LLM 降级整轮回落：写 P1 降级告警（alerts 表），绝不静默降级。"""
        from app.models.alert import Alert

        topic, _o, _f = _build_four_country_topic(db)
        annotator = _stub_annotator(degraded=True, engine_error=RuntimeError("不可用"))

        report = run_detection_cycle(db, llm_annotator=annotator, topic_ids=[topic.id])

        assert report.fallback_topics == 1
        alert = db.query(Alert).first()
        assert alert is not None
        assert alert.payload["kind"] == "llm_degraded"
        assert alert.payload["severity"] == "P1"
        assert "media_time_fallback" in alert.payload["reason"]


class TestDetectionWorker:
    def test_worker_maybe_detect_runs_cycle(self, db):
        """DetectionWorker 注入 session_factory/annotator 后到期即执行一轮检测。"""
        from sqlalchemy.orm import sessionmaker

        from app.worker.detection_worker import DetectionWorker

        topic, _o, _f = _build_four_country_topic(db)
        # worker finally 会 close() 会话：注入绑定同一引擎的独立 factory（对齐生产），
        # 夹具数据先提交，worker 的自治会话才可见（生产上议题本就是已提交数据）
        db.commit()
        annotator = _stub_annotator()
        fake_redis = MagicMock()
        fake_redis.smembers.return_value = set()
        fake_redis.sismember.return_value = False
        fake_redis.exists.return_value = 0

        worker = DetectionWorker(
            session_factory=sessionmaker(bind=db.get_bind()),
            redis_client=fake_redis,
            llm_annotator=annotator,
        )
        assert worker.maybe_detect() is True
        # 未到期不重复执行
        assert worker.maybe_detect() is False

        event = db.query(AgendaEvent).filter(AgendaEvent.topic_id == topic.id).first()
        assert event is not None
