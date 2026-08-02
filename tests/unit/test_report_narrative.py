"""T4.17 增强：build_topic_deep 报告叙述 LLM 生成 单元测试。

复用 test_agenda_merge.py 的 _FakeMergeLLM 风格写 _FakeNarrativeLLM：
  - engine.is_loaded=True，generate_structured 返回 ReportNarrativeOutput(narrative=...)
  - monitor.degraded 可开关
  - 可注入 generate 失败（抛 LLMError）验证回退

覆盖：
  1. llm 可用 → 概览含「分析要点：」叙述段（且 llm_judgements 留痕）
  2. llm=None → 概览无分析要点、不报错（行为与现状等价）
  3. llm degraded → 回退模板句（无分析要点），不报错
"""
import uuid
from datetime import UTC, datetime, timedelta

from app.models.article import Article
from app.models.topic import AgendaSnapshot, Topic, TopicArticle
from app.services.report_service import build_topic_deep
from tests.conftest import make_source

NOW = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)


class _FakeNarrativeLLM:
    """报告叙述 LLM 替身：narrative 可控，degraded / 失败 可开关。"""

    class _Engine:
        model_name = "test-model"
        is_loaded = True

        def __init__(self, parent):
            self._parent = parent

        def generate_structured(self, system, user, output_model):
            from app.llm.errors import LLMError
            from app.llm.schemas import ReportNarrativeOutput

            if self._parent._fail:
                raise LLMError("注入失败")
            if self._parent._empty:
                # 10 个空格：通过 schema 的 min_length=10，但 strip 后为空 → 回退
                return ReportNarrativeOutput(narrative="          "), 0.01
            return ReportNarrativeOutput(narrative=self._parent._narrative), 0.01

    class _Monitor:
        def __init__(self, degraded):
            self.degraded = degraded

        def record(self, *a, **k):
            pass

    def __init__(self, narrative="", degraded=False, fail=False, empty=False):
        self._narrative = narrative or (
            "该议题在多个国家获得显著报道，其中美国报道量领先，负面情感占比偏高，"
            "值得持续关注其后续进展。"
        )
        self._fail = fail
        self._empty = empty
        self.engine = self._Engine(self)
        self.monitor = self._Monitor(degraded)


def _make_topic(db, **kwargs) -> Topic:
    defaults = {
        "name": "测试议题",
        "name_auto": "测试议题",
        "name_zh": "测试议题（中文）",
        "topic_category": "政治安全",
        "naming_method": "llm",
        "cluster_method": "agglomerative",
        "keywords": ["测试", "关键词"],
        "summary_zh": "这是一段测试摘要。",
        "lifecycle_state": "forming",
        "confidence": "suspected",
        "country_scope": ["US", "CN"],
        "first_seen_at": NOW,
        "last_seen_at": NOW,
    }
    defaults.update(kwargs)
    topic = Topic(**defaults)
    db.add(topic)
    db.flush()
    return topic


def _add_snapshot(db, topic, cc, *, rank, count, neg=None, pos=None) -> None:
    db.add(AgendaSnapshot(
        country_code=cc,
        topic_id=topic.id,
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
        granularity="hour",
        article_count=count,
        salience_score=0.9,
        salience_rank=rank,
        sentiment_neg=neg,
        sentiment_pos=pos,
    ))
    db.flush()


def _add_article(db, topic, *, cc, published_at) -> Article:
    src = make_source(db, country_code=cc)
    a = Article(
        id=uuid.uuid4(),
        source_id=src.id,
        url=f"https://example.com/{uuid.uuid4().hex}",
        url_hash=uuid.uuid4().hex.ljust(64, "0")[:64],
        title="报道标题",
        content="正文内容" * 40,
        language="zh",
        published_at=published_at,
        country_code=cc,
        is_duplicate=False,
    )
    db.add(a)
    db.add(TopicArticle(topic_id=topic.id, article_id=a.id, weight=1.0, assign_method="online"))
    db.flush()
    return a


def _scope(topic: Topic) -> dict:
    return {
        "topic_id": str(topic.id),
        "from": (NOW - timedelta(days=1)).strftime("%Y-%m-%d"),
        "to": (NOW + timedelta(days=1)).strftime("%Y-%m-%d"),
    }


def _overview(report: dict) -> list[str]:
    return report["sections"][0]["paragraphs"]


class TestNarrativeWithLLM:
    def test_llm_available_appends_analysis_point(self, db):
        """llm 可用 → 概览含「分析要点：」，且 llm_judgements 留痕。"""
        topic = _make_topic(db)
        _add_snapshot(db, topic, "US", rank=1, count=42, neg=0.3, pos=0.5)
        _add_snapshot(db, topic, "CN", rank=2, count=17, neg=0.2, pos=0.6)
        _add_article(db, topic, cc="US", published_at=NOW - timedelta(hours=2))
        llm = _FakeNarrativeLLM()
        db.commit()

        report = build_topic_deep(db, _scope(topic), llm_annotator=llm)
        db.commit()

        paras = _overview(report)
        assert any(p.startswith("分析要点：") for p in paras)
        point = next(p for p in paras if p.startswith("分析要点："))
        assert "议题" in point  # 叙述来自 fake LLM
        # 留痕
        from app.models.llm import LLMJudgement

        rows = db.query(LLMJudgement).filter(LLMJudgement.task_type == "report_narrative").all()
        assert len(rows) == 1
        assert rows[0].success is True
        assert rows[0].topic_id == topic.id
        assert rows[0].output_payload["narrative"] == point[len("分析要点："):]

    def test_llm_none_no_analysis_point(self, db):
        """llm_annotator=None → 无分析要点、不报错。"""
        topic = _make_topic(db)
        _add_snapshot(db, topic, "US", rank=1, count=10, neg=0.1, pos=0.4)
        db.commit()

        report = build_topic_deep(db, _scope(topic))
        db.commit()

        paras = _overview(report)
        assert not any(p.startswith("分析要点：") for p in paras)
        # 不报错：section 结构完整
        assert report["sections"][0]["heading"] == "一、议题概览"
        assert any("摘要" in p for p in paras)

    def test_llm_degraded_no_analysis_point(self, db):
        """llm degraded → 回退模板句（无分析要点）、不报错。"""
        topic = _make_topic(db)
        _add_snapshot(db, topic, "US", rank=1, count=10, neg=0.1, pos=0.4)
        llm = _FakeNarrativeLLM(degraded=True)
        db.commit()

        report = build_topic_deep(db, _scope(topic), llm_annotator=llm)
        db.commit()

        paras = _overview(report)
        assert not any(p.startswith("分析要点：") for p in paras)
        assert any("统计窗口" in p for p in paras)

    def test_llm_failure_falls_back_no_analysis_point(self, db):
        """LLM 调用抛错 → 回退模板句（无分析要点）、不报错，留痕失败。"""
        topic = _make_topic(db)
        _add_snapshot(db, topic, "US", rank=1, count=10, neg=0.1, pos=0.4)
        llm = _FakeNarrativeLLM(fail=True)
        db.commit()

        report = build_topic_deep(db, _scope(topic), llm_annotator=llm)
        db.commit()

        paras = _overview(report)
        assert not any(p.startswith("分析要点：") for p in paras)
        from app.models.llm import LLMJudgement

        rows = db.query(LLMJudgement).filter(LLMJudgement.task_type == "report_narrative").all()
        assert len(rows) == 1
        assert rows[0].success is False

    def test_llm_empty_narrative_falls_back(self, db):
        """LLM 返回空 narrative → 不追加分析要点、不报错。"""
        topic = _make_topic(db)
        _add_snapshot(db, topic, "US", rank=1, count=10, neg=0.1, pos=0.4)
        llm = _FakeNarrativeLLM(empty=True)
        db.commit()

        report = build_topic_deep(db, _scope(topic), llm_annotator=llm)
        db.commit()

        paras = _overview(report)
        assert not any(p.startswith("分析要点：") for p in paras)
