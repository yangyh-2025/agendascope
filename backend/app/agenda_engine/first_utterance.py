"""LLM 首发表述判定器（T3.8，详细设计 4.2 算法 4 llm_first_utterance）。

职责：
- 给定议题 T 与实体 E、候选报道 article，调用 LLM 判定候选报道是否包含 E 对 T 的
  首发表述；
- 输入构造遵循详细设计：候选全文片段 + 实体历史表述摘要 + 议题背景，总预算
  ≤4000 token（超出截断候选片段，不裁剪历史表述）；
- 强制 evidence_quote 为候选片段原文子串；为空/不在原文 → 判定无效返回 None，
  进人工复核队列（revision_log 留痕，不创建 agenda_event）；
- LLM 不可用/降级 → 返回 None，detection_method 由调用方回落 media_time_fallback
  （详细设计 4.2 算法 4 注释）。

留痕（详细设计 3.2 关键不变量③）：每次判定（含失败）写 llm_judgements
（task_type='first_utterance'），含 input_payload 快照（议题/实体/候选 ID + token
预算 + 截断标记），output_payload 为 FirstUtteranceOutput 序列化结果或错误信息。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agenda_engine.config import AgendaSettings, get_agenda_settings
from app.agenda_engine.entity_repo import (
    get_recent_first_utterances,
    update_first_utterances,
)
from app.llm import prompts
from app.llm.errors import LLMError
from app.llm.schemas import FirstUtteranceOutput
from app.models.article import Article
from app.models.llm import LLMJudgement
from app.models.person import PersonOrg
from app.models.topic import Topic, TopicArticle

logger = structlog.get_logger(__name__)

# 检测方法标识（详细设计 4.2 算法 4）：LLM 判定 vs 媒体时间兜底
DETECTION_METHOD_LLM: Literal["llm_first_utterance"] = "llm_first_utterance"
DETECTION_METHOD_FALLBACK: Literal["media_time_fallback"] = "media_time_fallback"


@dataclass(frozen=True)
class FirstUtteranceVerdict:
    """LLM 首发表述判定结果（已写 first_utterances JSONB 后返回）。"""

    entity_id: uuid.UUID
    is_first_utterance: bool
    evidence_quote: str | None  # 强制非空（空判定已在 judge 函数内丢弃为 None）
    confidence: Literal["high", "medium", "low"]
    occurred_at: datetime | None  # LLM 推断首发时间；LLM 报空字符串时为 None
    reasoning: str
    model_name: str
    prompt_version: str


# ----------------------------------------------------------------------
# 候选片段构造（token 预算控制：超出截断候选，不裁剪历史表述）
# ----------------------------------------------------------------------
def _build_candidate_excerpt(
    article: Article,
    candidate_budget: int,
    token_counter: Any,  # 具 count_tokens 方法的对象（LLMEngine 或 stub）
) -> tuple[str, bool]:
    """构造候选全文片段（title + content），按 candidate_budget 截断。

    返回 (excerpt, truncated)；truncated=True 表示候选被截断（调用方在留痕中标记）。
    简化口径：字符级近似截断（中文 1 字符≈1 token、英文 4 字符≈1 token 的中位估值
    取 2 字符≈1 token），与 LLMEngine.count_tokens 未加载时的兜底口径一致。
    """
    title = (article.title or "").strip()
    content = (article.content or "").strip()
    excerpt = f"{title}\n\n{content}" if content else title
    if not excerpt:
        return "", False
    used = token_counter.count_tokens(excerpt)
    if used <= candidate_budget:
        return excerpt, False
    # 字符级截断：budget token × 2 字符，保留前缀（标题与首段对首发判定信息量最大）
    char_budget = candidate_budget * 2
    return excerpt[:char_budget], True


def _parse_occurred_at(raw: str) -> datetime | None:
    """解析 LLM 输出的 occurred_at（ISO 8601 字符串）；空/非法返回 None。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        # Python 3.11+ fromisoformat 支持 'Z' 与 '+HH:MM'
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _evidence_in_excerpt(quote: str, excerpt: str) -> bool:
    """evidence_quote 必须是候选片段原文子串（去首尾空白后严格子串匹配）。

    LLM 输出常带首尾空白或全角空格差异，统一 strip 后判；不改写、不翻译、
    不拼接——任何在 excerpt 中找不到的 quote 视为无效判定（返回 None 进人工队列）。
    """
    quote_clean = (quote or "").strip()
    if not quote_clean:
        return False
    return quote_clean in excerpt


# ----------------------------------------------------------------------
# 主接口
# ----------------------------------------------------------------------
def judge_first_utterance(
    db: Session,
    topic_id: uuid.UUID,
    entity_id: uuid.UUID,
    candidate_article_id: uuid.UUID,
    llm_annotator: Any,  # TopicAnnotator（依赖注入，复用 engine/monitor/settings）
    *,
    settings: AgendaSettings | None = None,
) -> FirstUtteranceVerdict | None:
    """LLM 判定 candidate_article 是否包含 entity 对 topic 的首发表述。

    返回 FirstUtteranceVerdict（成功且有效）或 None（无效/降级/不可用）。

    副作用：
    - 成功且 is_first_utterance=True：调用 update_first_utterances 把 quote
      追加进 persons_orgs.first_utterances（occurred_at 升序保持）；
    - 所有路径（含失败/无效）写 llm_judgements 留痕（详细设计 3.2 不变量③）；
    - 无效判定（evidence_quote 为空/不在候选原文中）在 topics.revision_log
      追加一条 kind='first_utterance_review' 的人工复核队列记录（不创建 agenda_event）。
    """
    agenda_settings = settings or get_agenda_settings()

    topic = db.get(Topic, topic_id)
    entity = db.get(PersonOrg, entity_id)
    article = db.get(Article, candidate_article_id)
    if topic is None or entity is None or article is None:
        raise KeyError(
            f"topic/entity/article 任一不存在: topic={topic_id} entity={entity_id} article={candidate_article_id}"
        )

    # 议题代表标题（按 assigned_at 升序取前 N 条作为议题背景）
    titles_stmt = (
        select(Article.title)
        .join(TopicArticle, TopicArticle.article_id == Article.id)
        .where(TopicArticle.topic_id == topic_id)
        .order_by(TopicArticle.assigned_at.asc())
        .limit(agenda_settings.first_utterance_topic_titles_limit)
    )
    topic_titles = [t for (t,) in db.execute(titles_stmt).all()]

    # 实体历史表述摘要（近 N 条 occurred_at 升序；不裁剪——详细设计硬性要求）
    history_quotes = get_recent_first_utterances(
        entity, limit=agenda_settings.first_utterance_history_limit
    )

    # 候选片段：截断到 candidate_budget（历史表述不裁剪；总预算超出由截断候选兜底）
    candidate_excerpt, truncated = _build_candidate_excerpt(
        article,
        agenda_settings.first_utterance_candidate_budget,
        llm_annotator.engine,
    )

    template = prompts.get_prompt(prompts.TASK_FIRST_UTTERANCE)
    model_name = llm_annotator.engine.model_name
    input_payload: dict[str, Any] = {
        "topic_id": str(topic_id),
        "topic_name": topic.name,
        "topic_titles": topic_titles,
        "entity_id": str(entity_id),
        "entity_name": entity.name,
        "entity_type": entity.entity_type,
        "country_code": entity.country_code,
        "candidate_article_id": str(candidate_article_id),
        "candidate_excerpt_chars": len(candidate_excerpt),
        "candidate_truncated": truncated,
        "history_quotes": history_quotes,
        "candidate_excerpt": candidate_excerpt,
    }

    def _record(
        *,
        success: bool,
        output: dict[str, Any] | None,
        error: str | None,
        latency_s: float,
    ) -> None:
        judgement = LLMJudgement(
            topic_id=topic_id,
            task_type=prompts.TASK_FIRST_UTTERANCE,
            model_name=model_name,
            prompt_version=template.version,
            input_payload=input_payload,
            output_payload=output,
            success=success,
            naming_method=None,  # 首发判定不涉及命名兜底链，置空（与命名/分类任务区分）
            error=error,
            latency_ms=int(latency_s * 1000),
        )
        db.add(judgement)
        db.flush()

    def _enqueue_human_review(reason: str) -> None:
        """无效判定进人工复核队列：topics.revision_log 追加记录，不创建 agenda_event。"""
        revision_log = list(topic.revision_log or [])
        revision_log.append(
            {
                "kind": "first_utterance_review",
                "entity_id": str(entity_id),
                "entity_name": entity.name,
                "candidate_article_id": str(candidate_article_id),
                "reason": reason,
                "actor": "machine",
                "model": model_name,
                "prompt_version": template.version,
                "at": datetime.now(UTC).isoformat(),
            }
        )
        topic.revision_log = revision_log
        db.flush()

    # LLM 降级 / 不可用 → 返回 None（detection_method 由调用方回落 media_time_fallback）
    if llm_annotator.monitor.degraded:
        _record(
            success=False,
            output=None,
            error=llm_annotator.monitor.reason or "llm_degraded",
            latency_s=0.0,
        )
        logger.info(
            "first_utterance_skipped_llm_degraded",
            topic_id=str(topic_id),
            entity_id=str(entity_id),
            article_id=str(candidate_article_id),
        )
        return None

    user_prompt = template.build_user(input_payload)
    started = datetime.now(UTC)
    started_mono = datetime.now(UTC).timestamp()
    try:
        if not llm_annotator.engine.is_loaded:
            llm_annotator.engine.load()
        parsed, latency_s = llm_annotator.engine.generate_structured(
            template.system, user_prompt, FirstUtteranceOutput
        )
    except LLMError as exc:
        llm_annotator.monitor.record(False, reason=str(exc)[:200])
        _record(success=False, output=None, error=str(exc)[:300], latency_s=0.0)
        logger.warning(
            "first_utterance_llm_failed",
            topic_id=str(topic_id),
            entity_id=str(entity_id),
            article_id=str(candidate_article_id),
            error=str(exc)[:200],
        )
        return None
    finally:
        _ = started, started_mono  # 保留入口时间戳位（占位防误删，无业务语义）

    llm_annotator.monitor.record(True)
    output_payload = {
        "is_first_utterance": parsed.is_first_utterance,
        "evidence_quote": parsed.evidence_quote,
        "confidence": parsed.confidence,
        "occurred_at": parsed.occurred_at,
        "reasoning": parsed.reasoning,
    }

    # 有效性校验：is_first_utterance=True 但 evidence_quote 为空/不在候选原文 → 丢弃进人工
    if parsed.is_first_utterance:
        quote = (parsed.evidence_quote or "").strip()
        if not quote:
            _record(
                success=False,
                output=output_payload,
                error="empty_evidence_quote",
                latency_s=latency_s,
            )
            _enqueue_human_review("empty_evidence_quote")
            logger.info(
                "first_utterance_rejected_empty_quote",
                topic_id=str(topic_id),
                entity_id=str(entity_id),
                article_id=str(candidate_article_id),
            )
            return None
        if not _evidence_in_excerpt(quote, candidate_excerpt):
            _record(
                success=False,
                output=output_payload,
                error="evidence_quote_not_in_excerpt",
                latency_s=latency_s,
            )
            _enqueue_human_review("evidence_quote_not_in_excerpt")
            logger.info(
                "first_utterance_rejected_quote_mismatch",
                topic_id=str(topic_id),
                entity_id=str(entity_id),
                article_id=str(candidate_article_id),
                quote=quote[:120],
            )
            return None

    # 有效判定：is_first_utterance=True → 写 first_utterances JSONB（occurred_at 升序）
    _record(success=True, output=output_payload, error=None, latency_s=latency_s)

    occurred_at = _parse_occurred_at(parsed.occurred_at)
    if parsed.is_first_utterance:
        # 首发时间回退：LLM 无法推断 → 用文章发布时间（客观锚点，不编造）
        update_first_utterances(
            db,
            entity_id,
            article_id=candidate_article_id,
            quote=parsed.evidence_quote.strip(),
            occurred_at=occurred_at or article.published_at,
            detection_method=DETECTION_METHOD_LLM,
            model=model_name,
            prompt_version=template.version,
        )

    return FirstUtteranceVerdict(
        entity_id=entity_id,
        is_first_utterance=parsed.is_first_utterance,
        evidence_quote=parsed.evidence_quote.strip() or None,
        confidence=parsed.confidence,
        occurred_at=occurred_at,
        reasoning=parsed.reasoning,
        model_name=model_name,
        prompt_version=template.version,
    )
