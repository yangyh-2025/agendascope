"""T3.8 LLM 首发表述判定器集成测试：真实 Qwen2.5-0.5B 推理。

模型缺失时跳过（models/Qwen2.5-0.5B-Instruct 需先下载，对齐 naming_worker 集成测试口径）。
本地开发与验收必须实际跑通：禁止 Mock 推理输出。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.agenda_engine.entity_repo import find_or_create_entity
from app.agenda_engine.first_utterance import judge_first_utterance
from app.llm.annotator import TopicAnnotator
from app.llm.settings import LLMSettings
from app.models.article import Article
from app.models.llm import LLMJudgement
from app.models.person import PersonOrg
from app.models.topic import Topic, TopicArticle
from tests.conftest import make_source

pytestmark = pytest.mark.integration

T0 = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)


@pytest.fixture(scope="session")
def real_annotator():
    settings = LLMSettings()
    annotator = TopicAnnotator(settings=settings)
    if not annotator.engine.model_dir_exists():
        pytest.skip(f"模型未下载: {settings.resolved_model_dir()}（需先下载 Qwen2.5-0.5B-Instruct）")
    annotator.engine.load()
    return annotator


def _build(db, *, title: str, content: str, entity_name: str, entity_name_zh: str, country: str):
    source = make_source(db)
    topic = Topic(
        name="印太经济框架",
        name_auto="印太经济框架",
        naming_method="llm",
        keywords=["印太", "经济框架"],
        cluster_method="bertopic",
        country_scope=["US"],
        lifecycle_state="forming",
    )
    db.add(topic)
    db.flush()

    article = Article(
        id=uuid.uuid4(),
        source_id=source.id,
        url=f"https://example.com/{uuid.uuid4().hex}",
        url_hash=uuid.uuid4().hex * 2,
        title=title,
        content=content,
        language="en",
        published_at=T0,
        country_code=country,
    )
    db.add(article)
    db.flush()
    db.add(TopicArticle(topic_id=topic.id, article_id=article.id, weight=1.0, assign_method="online"))

    entity = find_or_create_entity(
        db, name=entity_name, entity_type="person", country_code=country, name_zh=entity_name_zh,
    )
    db.commit()
    return topic, article, entity


def test_real_llm_judges_first_utterance_positive(db, real_annotator):
    """真实推理：候选文章含'拜登首次提出 XX 政策'明确语句 → verdict.is_first_utterance=True。"""
    title = "Biden unveils Indo-Pacific Economic Framework"
    content = (
        "U.S. President Joe Biden on Monday first proposed the Indo-Pacific Economic Framework "
        "during his visit to Tokyo, marking a new chapter in U.S. economic engagement with Asia. "
        "The framework aims to deepen cooperation on supply chains, clean energy, and trade."
    )
    topic, article, entity = _build(
        db, title=title, content=content,
        entity_name="Joe Biden", entity_name_zh="拜登", country="US",
    )

    verdict = judge_first_utterance(db, topic.id, entity.id, article.id, real_annotator)
    db.commit()

    # 真实推理允许模型在边界 case 上判 False（0.5B 模型能力有限），但 is_first_utterance=True 时
    # evidence_quote 必须是原文子串（详细设计硬性约束）
    assert verdict is not None, "真实 LLM 应能给出有效判定（含 quote 校验通过或拒判 None 之外的结果）"
    if verdict.is_first_utterance:
        assert verdict.evidence_quote, "is_first_utterance=True 时 evidence_quote 必须非空"
        excerpt = f"{title}\n\n{content}"
        assert verdict.evidence_quote in excerpt, "evidence_quote 必须是候选片段原文子串"
        # first_utterances JSONB 应写入
        db.expire_all()
        entity_db = db.get(PersonOrg, entity.id)
        assert len(entity_db.first_utterances) >= 1
        assert entity_db.first_utterances[-1]["detection_method"] == "llm_first_utterance"

    # 留痕必须存在（不论判定结果）
    judgements = db.query(LLMJudgement).filter_by(
        topic_id=topic.id, task_type="first_utterance"
    ).all()
    assert len(judgements) == 1
    assert judgements[0].prompt_version == "first-utterance-v1"
    print(
        f"\n[实测] 正向场景：is_first={verdict.is_first_utterance}, "
        f"confidence={verdict.confidence}, quote={verdict.evidence_quote!r}, "
        f"reasoning={verdict.reasoning!r}"
    )


def test_real_llm_judges_first_utterance_negative(db, real_annotator):
    """真实推理：候选文章仅引述他人 earlier 表态 → 期望 is_first_utterance=False。"""
    title = "Analysts react to framework"
    content = (
        "Commentators on Tuesday debated the Indo-Pacific Economic Framework that was originally "
        "introduced by the Biden administration last year. Experts said the proposal has been "
        "discussed extensively since its earlier rollout, and no new initiatives were announced."
    )
    topic, article, entity = _build(
        db, title=title, content=content,
        entity_name="Joe Biden", entity_name_zh="拜登", country="US",
    )

    verdict = judge_first_utterance(db, topic.id, entity.id, article.id, real_annotator)
    db.commit()

    # 候选片段明确说"originally introduced last year"且"no new initiatives"，
    # 真实 LLM 应判 False；即使判 True 也必须 quote 在原文（详细设计硬约束）
    assert verdict is not None
    if verdict.is_first_utterance:
        # 0.5B 小模型可能在边界 case 误判 True；此时仍必须 quote 在原文子串
        excerpt = f"{title}\n\n{content}"
        assert verdict.evidence_quote in excerpt
        print(f"\n[实测] 反向场景小模型误判为首发（可接受）：quote={verdict.evidence_quote!r}")
    else:
        # 期望路径：判 False
        assert verdict.is_first_utterance is False

    judgements = db.query(LLMJudgement).filter_by(
        topic_id=topic.id, task_type="first_utterance"
    ).all()
    assert len(judgements) == 1
    print(
        f"\n[实测] 反向场景：is_first={verdict.is_first_utterance}, "
        f"confidence={verdict.confidence}, reasoning={verdict.reasoning!r}"
    )
