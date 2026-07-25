"""T3.8 LLM 首发表述判定器单元测试（stub LLM 引擎注入，不真推理）。

测试通过 stub 引擎替换 LLMEngine.generate_structured/count_tokens/model_name，
验证编排逻辑：预算控制、evidence_quote 校验、无效判定进人工复核队列、
留痕（llm_judgements + topics.revision_log）、first_utterances JSONB 写回。
禁止 Mock 业务逻辑——DB 会话、persons_orgs 表操作均为真实 SQLAlchemy 调用。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from app.agenda_engine.entity_repo import find_or_create_entity
from app.agenda_engine.first_utterance import (
    DETECTION_METHOD_LLM,
    judge_first_utterance,
)
from app.llm.annotator import TopicAnnotator
from app.llm.errors import LLMUnavailableError
from app.llm.schemas import FirstUtteranceOutput
from app.llm.settings import LLMSettings
from app.models.article import Article
from app.models.llm import LLMJudgement
from app.models.person import PersonOrg
from app.models.topic import Topic, TopicArticle
from tests.conftest import make_source

T0 = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)


# ----------------------------------------------------------------------
# Stub LLM 引擎（不真推理，仅返回预设 FirstUtteranceOutput）
# ----------------------------------------------------------------------
class _StubEngine:
    """stub LLM 引擎：通过脚本化输出响应（不调用真实权重）。"""

    def __init__(self, script: list[FirstUtteranceOutput | Exception]):
        self._script = list(script)
        self.calls: list[tuple[str, str]] = []  # (system, user) 调用留痕
        self._model_name = "StubQwen-0.5B"

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def is_loaded(self) -> bool:
        return True

    def load(self) -> None:
        return None

    def count_tokens(self, text: str) -> int:
        # 与 LLMEngine 未加载时口径一致：2 字符≈1 token
        return max(1, len(text) // 2)

    def generate_structured(
        self, system: str, user: str, output_model: type[BaseModel], max_retries: int = 1,
    ) -> tuple[Any, float]:
        self.calls.append((system, user))
        if not self._script:
            raise LLMUnavailableError("stub 引擎脚本耗尽")
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome, 0.05


def _make_annotator(engine: _StubEngine) -> TopicAnnotator:
    """组装 stub 引擎的 TopicAnnotator（不真加载模型，仅复用 settings/monitor）。

    通过 __init__ 正常构造（engine 注入），避免绕过构造函数的 hack。
    """
    settings = LLMSettings(model_dir="models/__stub__")
    return TopicAnnotator(engine=engine, settings=settings)  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# 夹具：真实 DB 建 Topic / Article / PersonOrg
# ----------------------------------------------------------------------
@dataclass
class _Fixture:
    topic: Topic
    article: Article
    entity: PersonOrg


def _build_fixture(db, *, article_title: str, article_content: str) -> _Fixture:
    source = make_source(db)
    topic = Topic(
        name="对台军售升级",
        name_auto="对台军售升级",
        naming_method="llm",
        keywords=["对台军售"],
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
        title=article_title,
        content=article_content,
        language="en",
        published_at=T0,
        country_code="US",
    )
    db.add(article)
    db.flush()
    db.add(TopicArticle(topic_id=topic.id, article_id=article.id, weight=1.0, assign_method="online"))

    entity = find_or_create_entity(
        db, name="Joe Biden", entity_type="person", country_code="US", name_zh="拜登",
    )
    db.flush()
    return _Fixture(topic=topic, article=article, entity=entity)


# ----------------------------------------------------------------------
# 场景 1：候选片段含首发证据 → 返回 verdict，evidence_quote 是原文子串
# ----------------------------------------------------------------------
def test_first_utterance_positive_writes_history_and_returns_verdict(db):
    quote = "Biden first proposed the Indo-Pacific Economic Framework"
    fixture = _build_fixture(
        db,
        article_title="Biden unveils new economic framework",
        article_content=(
            f"{quote} during the summit on Monday, marking a shift in US trade policy."
        ),
    )
    output = FirstUtteranceOutput(
        is_first_utterance=True,
        evidence_quote=quote,
        confidence="high",
        occurred_at="2026-07-20T08:00:00+00:00",
        reasoning="候选片段明确提到 Biden 首次提出该框架",
    )
    annotator = _make_annotator(_StubEngine([output]))

    verdict = judge_first_utterance(
        db, fixture.topic.id, fixture.entity.id, fixture.article.id, annotator,
    )
    assert verdict is not None
    assert verdict.is_first_utterance is True
    assert verdict.evidence_quote == quote
    assert verdict.evidence_quote in fixture.article.content  # 是原文子串
    assert verdict.confidence == "high"
    assert verdict.model_name == "StubQwen-0.5B"
    assert verdict.prompt_version == "first-utterance-v1"

    # first_utterances JSONB 已写入
    db.expire_all()
    entity_db = db.get(PersonOrg, fixture.entity.id)
    assert len(entity_db.first_utterances) == 1
    record = entity_db.first_utterances[0]
    assert record["quote"] == quote
    assert record["detection_method"] == DETECTION_METHOD_LLM
    assert record["model"] == "StubQwen-0.5B"
    assert record["prompt_version"] == "first-utterance-v1"
    assert record["article_id"] == str(fixture.article.id)

    # llm_judgements 留痕
    judgements = db.query(LLMJudgement).filter_by(
        topic_id=fixture.topic.id, task_type="first_utterance"
    ).all()
    assert len(judgements) == 1
    assert judgements[0].success is True
    assert judgements[0].prompt_version == "first-utterance-v1"


# ----------------------------------------------------------------------
# 场景 2：候选片段无首发证据 → 返回 None 不进历史，但留痕失败
# ----------------------------------------------------------------------
def test_first_utterance_negative_returns_none_and_no_history(db):
    fixture = _build_fixture(
        db,
        article_title="Analysts discuss the framework",
        article_content=(
            "Commentators debated the proposal that was originally introduced earlier this year."
        ),
    )
    output = FirstUtteranceOutput(
        is_first_utterance=False,
        evidence_quote="",
        confidence="medium",
        occurred_at="",
        reasoning="候选片段仅引述他人观点，未见 Biden 主动首发表述",
    )
    annotator = _make_annotator(_StubEngine([output]))

    verdict = judge_first_utterance(
        db, fixture.topic.id, fixture.entity.id, fixture.article.id, annotator,
    )
    # is_first_utterance=False 仍返回 verdict（LLM 成功判定为"非首发"）
    assert verdict is not None
    assert verdict.is_first_utterance is False
    assert verdict.evidence_quote is None

    # first_utterances 不应新增（非首发不写入）
    db.expire_all()
    entity_db = db.get(PersonOrg, fixture.entity.id)
    assert entity_db.first_utterances == []

    # llm_judgements 留痕成功（LLM 判定为"非首发"是有效判定）
    judgements = db.query(LLMJudgement).filter_by(
        topic_id=fixture.topic.id, task_type="first_utterance"
    ).all()
    assert len(judgements) == 1
    assert judgements[0].success is True


# ----------------------------------------------------------------------
# 场景 3：evidence_quote 不在候选片段原文 → 判定无效返回 None，进人工队列
# ----------------------------------------------------------------------
def test_first_utterance_quote_not_in_excerpt_rejected(db):
    fixture = _build_fixture(
        db,
        article_title="Biden speaks",
        article_content="Biden mentioned the policy briefly.",
    )
    output = FirstUtteranceOutput(
        is_first_utterance=True,
        evidence_quote="This quote was hallucinated by the model and never appeared",
        confidence="low",
        occurred_at="",
        reasoning="stub 引擎伪造 quote",
    )
    annotator = _make_annotator(_StubEngine([output]))

    verdict = judge_first_utterance(
        db, fixture.topic.id, fixture.entity.id, fixture.article.id, annotator,
    )
    assert verdict is None, "evidence_quote 不在原文中 → 判定无效"

    # 进人工复核队列（topics.revision_log 追加 kind=first_utterance_review）
    db.expire_all()
    topic_db = db.get(Topic, fixture.topic.id)
    reviews = [r for r in topic_db.revision_log if r.get("kind") == "first_utterance_review"]
    assert len(reviews) == 1
    assert reviews[0]["reason"] == "evidence_quote_not_in_excerpt"
    assert reviews[0]["entity_id"] == str(fixture.entity.id)
    assert reviews[0]["candidate_article_id"] == str(fixture.article.id)

    # first_utterances 不应新增
    entity_db = db.get(PersonOrg, fixture.entity.id)
    assert entity_db.first_utterances == []

    # llm_judgements 留痕失败（带错误原因）
    judgements = db.query(LLMJudgement).filter_by(
        topic_id=fixture.topic.id, task_type="first_utterance"
    ).all()
    assert len(judgements) == 1
    assert judgements[0].success is False
    assert judgements[0].error == "evidence_quote_not_in_excerpt"


def test_first_utterance_empty_quote_rejected(db):
    """is_first_utterance=True 但 evidence_quote 空 → 判定无效返回 None，进人工队列。"""
    fixture = _build_fixture(
        db,
        article_title="Biden speaks",
        article_content="Biden announced the new policy today.",
    )
    output = FirstUtteranceOutput(
        is_first_utterance=True,
        evidence_quote="",
        confidence="low",
        occurred_at="",
        reasoning="stub 输出空 quote",
    )
    annotator = _make_annotator(_StubEngine([output]))
    verdict = judge_first_utterance(
        db, fixture.topic.id, fixture.entity.id, fixture.article.id, annotator,
    )
    assert verdict is None

    db.expire_all()
    topic_db = db.get(Topic, fixture.topic.id)
    reviews = [r for r in topic_db.revision_log if r.get("kind") == "first_utterance_review"]
    assert len(reviews) == 1
    assert reviews[0]["reason"] == "empty_evidence_quote"


# ----------------------------------------------------------------------
# 场景 4：候选片段 token 超预算 → 截断候选但不裁剪历史表述
# ----------------------------------------------------------------------
def test_first_utterance_budget_truncates_candidate_not_history(db):
    """候选全文 6000 字符（≈3000 token）超过 2000 token 预算 → 截断候选；
    实体历史表述 5 条全部保留在 prompt 中（不裁剪）。
    """
    # 构造 6000 字符的 content
    long_content = "Biden proposed the framework. " * 200  # 30 字 × 200 = 6000 字符
    fixture = _build_fixture(
        db,
        article_title="Biden long speech",
        article_content=long_content,
    )
    # 预写 5 条历史表述（详细设计要求"不裁剪历史表述"）
    for i in range(5):
        quote_i = f"historical quote {i}"
        fixture.entity.first_utterances = list(fixture.entity.first_utterances or []) + [{
            "article_id": str(uuid.uuid4()),
            "quote": quote_i,
            "occurred_at": datetime(2026, 7, 10 + i, tzinfo=UTC).isoformat(),
            "detection_method": "llm_first_utterance",
            "model": "old",
            "prompt_version": "first-utterance-v1",
            "created_at": datetime.now(UTC).isoformat(),
        }]
    db.flush()

    quote_in_head = "Biden proposed the framework."
    output = FirstUtteranceOutput(
        is_first_utterance=True,
        evidence_quote=quote_in_head,  # 在截断前 4000 字符内
        confidence="high",
        occurred_at="",
        reasoning="截断后仍可见首发表述",
    )
    stub = _StubEngine([output])
    annotator = _make_annotator(stub)

    verdict = judge_first_utterance(
        db, fixture.topic.id, fixture.entity.id, fixture.article.id, annotator,
    )
    assert verdict is not None
    assert verdict.is_first_utterance is True

    # 验证 prompt 内容：候选被截断、历史 5 条全保留
    assert len(stub.calls) == 1
    _system, user_prompt = stub.calls[0]
    # 候选原文 6000 字符，被截断后应显著短于原文
    # （截断预算 2000 token × 2 字符/token = 4000 字符）
    assert len(user_prompt) < len(long_content), "候选片段应被截断，prompt 长度小于原文"
    # 历史 5 条全保留
    for i in range(5):
        assert f"historical quote {i}" in user_prompt, f"历史表述 {i} 不得被裁剪"

    # 留痕 input_payload 标记 candidate_truncated=True
    judgements = db.query(LLMJudgement).filter_by(
        topic_id=fixture.topic.id, task_type="first_utterance"
    ).all()
    assert judgements[0].input_payload["candidate_truncated"] is True
    assert len(judgements[0].input_payload["history_quotes"]) == 5


# ----------------------------------------------------------------------
# 场景 5：LLM 不可用 → 返回 None + 失败留痕
# ----------------------------------------------------------------------
def test_first_utterance_llm_unavailable_returns_none(db):
    fixture = _build_fixture(
        db,
        article_title="Biden speaks",
        article_content="Biden announced the policy.",
    )
    stub = _StubEngine([LLMUnavailableError("模型未加载")])
    annotator = _make_annotator(stub)

    verdict = judge_first_utterance(
        db, fixture.topic.id, fixture.entity.id, fixture.article.id, annotator,
    )
    assert verdict is None

    # 留痕失败
    judgements = db.query(LLMJudgement).filter_by(
        topic_id=fixture.topic.id, task_type="first_utterance"
    ).all()
    assert len(judgements) == 1
    assert judgements[0].success is False
    assert "模型未加载" in judgements[0].error

    # first_utterances 不应新增
    entity_db = db.get(PersonOrg, fixture.entity.id)
    assert entity_db.first_utterances == []
