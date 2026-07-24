"""LLM 标注落库 / 降级告警 / 回填 / 版本重跑对比集成测试（T2.16/T2.17）。

需要本地 docker compose 的 postgres/redis；使用独立测试库与 redis db（见 conftest）。
引擎仅打桩 ``_generate`` 原始生成；落库、告警、回填、重跑逻辑全部真实。
"""
import hashlib
import uuid
from datetime import UTC, datetime

import pytest

from app.llm.alerting import SYSTEM_LLM_HEALTH_RULE
from app.llm.annotator import NAMING_FALLBACK, NAMING_LLM, TopicAnnotator
from app.llm.engine import LLMEngine
from app.llm.health import DegradationMonitor
from app.llm.settings import LLMSettings
from app.models.alert import Alert, AlertRule
from app.models.article import Article
from app.models.llm import LLMJudgement
from app.models.topic import Topic, TopicArticle


class StubEngine(LLMEngine):
    def __init__(self, outputs: list[str], settings: LLMSettings):
        super().__init__(settings)
        self._outputs = list(outputs)

    @property
    def is_loaded(self) -> bool:  # type: ignore[override]
        return True

    @property
    def model_name(self) -> str:  # type: ignore[override]
        return "stub-qwen"

    def load(self) -> None:
        return None

    def _generate(self, messages, max_new_tokens=None) -> str:
        if not self._outputs:
            raise AssertionError("StubEngine 输出脚本已耗尽")
        return self._outputs.pop(0)


TITLES = [
    "俄乌双方就停火协议展开新一轮谈判",
    "俄乌谈判在伊斯坦布尔重启 停火成焦点",
    "乌克兰与俄罗斯代表就停火条件交换意见",
    "多方斡旋推动俄乌停火谈判取得进展",
    "欧洲多国呼吁俄乌尽快达成停火",
]
TOP_WORDS = ["停火", "谈判", "俄乌"]


@pytest.fixture()
def llm_db(db, migrated_db):
    """在共享 db 夹具基础上追加清理 LLM 相关表。"""
    from sqlalchemy import text

    with migrated_db.connect() as conn:
        for table in ("llm_judgements", "topic_articles", "topics", "articles", "alerts", "alert_rules", "sources", "users"):
            conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
        conn.commit()
    return db


def _make_topic(db, **overrides) -> Topic:
    defaults = {
        "name": "关键词:停火·谈判",
        "name_auto": "关键词:停火·谈判",
        "naming_method": NAMING_FALLBACK,
        "keywords": TOP_WORDS,
    }
    defaults.update(overrides)
    topic = Topic(**defaults)
    db.add(topic)
    db.flush()
    return topic


def _make_article(db, source_id, title: str) -> Article:
    article = Article(
        source_id=source_id,
        url=f"https://example.com/{uuid.uuid4().hex}",
        url_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        title=title,
        language="zh",
        published_at=datetime.now(UTC),
        country_code="US",
    )
    db.add(article)
    db.flush()
    return article


def test_persist_annotation_llm_path(llm_db):
    annotator = TopicAnnotator(
        engine=StubEngine([
            '{"name": "俄乌停火谈判"}',
            '{"category": "政治安全"}',
            '{"summary": "俄乌双方重启停火谈判。多方斡旋下谈判取得进展。"}',
        ], LLMSettings()),
        settings=LLMSettings(),
    )
    topic = _make_topic(llm_db)
    annotation = annotator.annotate_topic(TITLES, TOP_WORDS)
    annotator.persist_annotation(llm_db, topic, annotation)
    llm_db.commit()

    assert topic.name_auto == "俄乌停火谈判"
    assert topic.name == "俄乌停火谈判"
    assert topic.topic_category == "政治安全"
    assert topic.summary_zh and "谈判" in topic.summary_zh
    assert topic.naming_method == NAMING_LLM
    assert topic.llm_model == "stub-qwen"
    assert topic.prompt_version == "topic-naming-v1"

    judgements = llm_db.query(LLMJudgement).filter_by(topic_id=topic.id).all()
    assert len(judgements) == 3, "命名/分类/摘要各留痕一条"
    assert all(j.model_name == "stub-qwen" for j in judgements)
    assert {j.task_type for j in judgements} == {"topic_naming", "topic_category", "topic_summary"}
    naming = next(j for j in judgements if j.task_type == "topic_naming")
    assert naming.input_payload["titles"] == TITLES
    assert naming.output_payload["value"] == "俄乌停火谈判"


def test_persist_annotation_degraded_writes_p1_alert(llm_db, redis_client):
    settings = LLMSettings()
    monitor = DegradationMonitor(settings)
    monitor.mark_unavailable("模型目录不存在")
    annotator = TopicAnnotator(engine=StubEngine([], settings), monitor=monitor, settings=settings)

    topic = _make_topic(llm_db)
    annotation = annotator.annotate_topic(TITLES, TOP_WORDS)
    assert annotation.degraded
    annotator.persist_annotation(llm_db, topic, annotation, redis_client=redis_client)
    llm_db.commit()

    assert topic.naming_method == NAMING_FALLBACK
    assert str(topic.name_auto).startswith("关键词:")

    alerts = llm_db.query(Alert).all()
    assert len(alerts) == 1, "降级必须写 P1 告警"
    payload = alerts[0].payload
    assert payload["kind"] == "llm_degraded"
    assert payload["severity"] == "P1"
    assert payload["fallback"] == "ctfidf_fallback"
    rule = llm_db.query(AlertRule).filter_by(name=SYSTEM_LLM_HEALTH_RULE).one()
    assert alerts[0].rule_id == rule.id

    # 防抖：第二次降级持久化不重复告警（redis 防抖键 1h 内有效）
    topic2 = _make_topic(llm_db)
    annotator.persist_annotation(llm_db, topic2, annotator.annotate_topic(TITLES, TOP_WORDS), redis_client=redis_client)
    llm_db.commit()
    assert llm_db.query(Alert).count() == 1


def test_human_locked_name_not_overwritten(llm_db):
    annotator = TopicAnnotator(
        engine=StubEngine([
            '{"name": "俄乌停火谈判"}',
            '{"category": "政治安全"}',
            '{"summary": "俄乌双方重启停火谈判。多方斡旋下谈判取得进展。"}',
        ], LLMSettings()),
        settings=LLMSettings(),
    )
    topic = _make_topic(llm_db, name="人工命名", human_locked_fields=["name"])
    annotator.persist_annotation(llm_db, topic, annotator.annotate_topic(TITLES, TOP_WORDS))
    llm_db.commit()
    assert topic.name == "人工命名", "人工锁定字段不得被机器推翻"
    assert topic.name_auto == "俄乌停火谈判", "自动名仍留痕备查"


def _make_source(db):
    from app.models.source import Source

    source = Source(
        name=f"LLM Test Media {uuid.uuid4().hex[:6]}",
        country_code="US",
        homepage_url="https://example.com",
        feed_url=f"https://example.com/feed-{uuid.uuid4().hex[:8]}.xml",
        collect_mode="rss",
        adapter_type="rss",
        media_type="online",
        language="en",
        poll_interval_min=5,
        audience_weight=10.0,
    )
    db.add(source)
    db.flush()
    return source


def test_backfill_degraded_topics(llm_db, migrated_db):
    source = _make_source(llm_db)
    topic = _make_topic(llm_db)
    for title in TITLES:
        article = _make_article(llm_db, source.id, title)
        llm_db.add(TopicArticle(topic_id=topic.id, article_id=article.id))
    llm_db.commit()

    # LLM 恢复后的 annotator（真实逻辑，桩输出）
    annotator = TopicAnnotator(
        engine=StubEngine([
            '{"name": "俄乌停火谈判"}',
            '{"category": "政治安全"}',
            '{"summary": "俄乌双方重启停火谈判。多方斡旋下谈判取得进展。"}',
        ], LLMSettings()),
        settings=LLMSettings(),
    )
    count = annotator.backfill_degraded_topics(llm_db)
    llm_db.commit()
    assert count == 1
    assert topic.naming_method == NAMING_LLM
    assert topic.name_auto == "俄乌停火谈判"
    assert topic.topic_category == "政治安全"
    assert topic.summary_zh
    assert topic.revision_log, "回填必须写 revision_log"
    entry = topic.revision_log[-1]
    assert entry["trigger"] == "llm_recovered_backfill"
    assert entry["model"] == "stub-qwen"
    assert entry["prompt_version"] == "topic-naming-v1"


def test_rerun_judgements_comparison(llm_db):
    # 先用 v1 留痕一组判定（命名/分类/摘要各一条）
    annotator = TopicAnnotator(
        engine=StubEngine([
            '{"name": "俄乌停火谈判"}',
            '{"category": "政治安全"}',
            '{"summary": "俄乌双方重启停火谈判。多方斡旋下谈判取得进展。"}',
        ], LLMSettings()),
        settings=LLMSettings(),
    )
    topic = _make_topic(llm_db)
    annotation = annotator.annotate_topic(TITLES, TOP_WORDS)
    annotator.persist_annotation(llm_db, topic, annotation)
    llm_db.commit()

    # 用新版本 prompt（复用注册表中的 v1 版本号模拟指定版本重跑）批量重跑对比
    rerunner = TopicAnnotator(
        engine=StubEngine(['{"name": "俄乌新一轮停火谈判"}'], LLMSettings()),
        settings=LLMSettings(),
    )
    comparisons = rerunner.rerun_judgements(llm_db, "topic_naming", "topic-naming-v1")
    llm_db.commit()
    assert len(comparisons) == 1
    item = comparisons[0]
    assert item["old_value"] == "俄乌停火谈判"
    assert item["new_value"] == "俄乌新一轮停火谈判"
    assert item["changed"] is True
    # 重跑结果留痕且关联基线，不改动 topics 现行值
    reruns = (
        llm_db.query(LLMJudgement)
        .filter(LLMJudgement.input_payload["rerun_of"].astext == item["judgement_id"])
        .all()
    )
    assert len(reruns) == 1
    assert llm_db.get(Topic, topic.id).name_auto == "俄乌停火谈判"
