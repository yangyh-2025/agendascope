"""LLM 终审审查官（T3.12，详细设计 4.2 算法 4 llm_final_review + PRD 8.5 降级链）。

对 suspected 议程设置事件评逻辑连贯性 1-10 分：
  - score ≥5 → 维持 suspected（事件证据链进入人工复核队列等待确认）
  - score <5 → 自动降为 watching 或驳回 REJECTED；驳回样本作负例积累
    （写入 final_review.verdict='rejected' 并保留事件供复盘，不删除）
  - 终审不可用 → 跳过终审直进人工复核队列（status 维持 suspected），
    不自动告警（PRD 8.5 降级链：LLM 终审不可用时事件直进人工复核队列）

prompt 版本化：final-review-v1 注册进 PROMPT_REGISTRY（只增不改），与 T2.17
prompt 版本管理一致；每次终审写 llm_judgements（模型名+prompt_version+输入/输出
快照+耗时+成败）与 event.final_review（score/verdict/model/prompt_version/reviewed_at）。
"""
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agenda_engine.event import AgendaEvent
from app.core.logging import get_logger
from app.llm.prompts import TASK_FINAL_REVIEW, get_prompt
from app.llm.schemas import schema_instruction

logger = get_logger("agenda.final_review")

VerdictType = Literal["completed", "rejected", "skipped_unavailable"]


class FinalReviewOutput(BaseModel):
    """LLM 终审输出（强制 JSON Schema）。"""

    score: int = Field(ge=1, le=10, description="逻辑连贯性评分 1-10，5 分为通过阈值")
    verdict: Literal["completed", "rejected"] = Field(
        description="completed=评分≥5 维持 suspected；rejected=评分<5 自动降疑似/驳回"
    )
    reasoning: str = Field(
        max_length=500,
        description="评分理由（≤500 字）：首发源是否可靠、跟随链路是否合理、统计是否支撑、是否存在更可能的非议程设置解释",
    )
    concerns: list[str] = Field(
        default_factory=list,
        description="主要疑虑点列表（无则空数组）：供分析师复核参考",
    )


@dataclass(frozen=True)
class FinalReviewResult:
    """终审结论。"""

    event_id: UUID
    verdict: VerdictType
    score: int | None  # skipped_unavailable 时为 None
    reasoning: str | None
    concerns: list[str]
    model_name: str | None
    prompt_version: str
    reviewed_at: datetime


def _build_review_input(event: AgendaEvent, topic_name: str) -> dict:
    """终审 prompt 输入构造（≤2000 token，超出截 follower_sequence）。"""
    followers = event.follower_sequence or []
    if len(followers) > 10:
        followers = followers[:10]
    stats = event.stats_evidence or {}
    return {
        "topic_name": topic_name,
        "origin_type": event.origin_type,
        "origin_country_code": event.origin_country_code,
        "origin_at": event.origin_at.isoformat() if event.origin_at else None,
        "origin_confidence": event.origin_confidence,
        "origin_quote": event.origin_quote,
        "follower_count": len(followers),
        "follower_sequence": followers,
        "stats_evidence": stats,
        "detection_method": event.detection_method,
    }


def review_event(
    db: Session,
    event: AgendaEvent,
    *,
    topic_name: str,
    llm_annotator,  # TopicAnnotator 实例（依赖注入）
    now: datetime | None = None,
) -> FinalReviewResult:
    """对 suspected 事件跑一次 LLM 终审。

    流程：
    1. 若 event.status != 'suspected'：跳过重审（返回 verdict='skipped_unavailable' 无 score）
    2. 调 llm_annotator 的 generate_structured 走 first-review prompt
    3. 解析输出 → score/verdict/reasoning/concerns
    4. score < 5 或 verdict='rejected'：
       - event.status = 'watching'（自动降疑似，不自动告警）
       - final_review.verdict = 'rejected'
       - 驳回样本作负例积累（revision_log 不新增——驳回本身不是修正；
         final_review 字段即留痕）
    5. score ≥ 5 且 verdict='completed'：
       - event.status 维持 suspected（等人工确认）
       - final_review.verdict = 'completed'
    6. event.final_review = {score, verdict, model, prompt_version, reviewed_at, reasoning, concerns}
    7. 返回 FinalReviewResult
    """
    now = now or datetime.now(UTC)
    if event.status != "suspected":
        logger.info(
            "final_review_skip_status",
            event_id=str(event.id), status=event.status,
        )
        return FinalReviewResult(
            event_id=event.id, verdict="skipped_unavailable", score=None,
            reasoning=None, concerns=[], model_name=None,
            prompt_version="final-review-v1", reviewed_at=now,
        )

    # LLM 调用（真实推理，解析失败重试 1 次复用 annotator 链路）
    prompt_template = get_prompt(TASK_FINAL_REVIEW)
    input_payload = _build_review_input(event, topic_name)
    try:
        output: FinalReviewOutput = llm_annotator.engine.generate_structured(
            prompt_template.system,
            prompt_template.build_user(input_payload),
            FinalReviewOutput,
            max_retries=1,
        )
        model_name = llm_annotator.settings.resolved_model_name()
    except Exception as exc:  # noqa: BLE001 终审不可用不阻塞事件流
        logger.warning(
            "final_review_llm_unavailable",
            event_id=str(event.id), error=str(exc)[:300],
        )
        # 降级：跳过终审直进人工复核队列（PRD 8.5），不自动告警
        event.final_review = {
            "score": None,
            "verdict": "skipped_unavailable",
            "model": None,
            "prompt_version": prompt_template.version,
            "reviewed_at": now.isoformat(),
            "reasoning": f"终审不可用：{str(exc)[:200]}",
            "concerns": [],
        }
        db.flush()
        return FinalReviewResult(
            event_id=event.id, verdict="skipped_unavailable", score=None,
            reasoning=None, concerns=[], model_name=None,
            prompt_version=prompt_template.version, reviewed_at=now,
        )

    # 判定分支
    passed = output.score >= 5 and output.verdict == "completed"
    verdict: VerdictType = "completed" if passed else "rejected"
    if not passed:
        # 自动降疑似为 watching，不自动告警
        event.status = "watching"
        event.confidence = "watching"

    event.final_review = {
        "score": output.score,
        "verdict": verdict,
        "model": model_name,
        "prompt_version": prompt_template.version,
        "reviewed_at": now.isoformat(),
        "reasoning": output.reasoning,
        "concerns": output.concerns,
    }
    db.flush()
    logger.info(
        "final_review_done",
        event_id=str(event.id), score=output.score, verdict=verdict,
    )
    return FinalReviewResult(
        event_id=event.id, verdict=verdict, score=output.score,
        reasoning=output.reasoning, concerns=output.concerns,
        model_name=model_name, prompt_version=prompt_template.version, reviewed_at=now,
    )


__all__ = [
    "FinalReviewOutput",
    "FinalReviewResult",
    "review_event",
    "schema_instruction",  # re-export 供 prompt 模板使用
]
