"""LLM 终审审查官（T3.12，详细设计 4.2 算法 4 llm_final_review + PRD 8.5 降级链）。

对 suspected 议程设置事件评逻辑连贯性 1-10 分，三档处置：
  - score ≥ 7 且 verdict='completed' → **自动 confirmed**(LLM 高置信，替代人工确认,
    revision_log 记 'auto_confirmed_by_llm' 留痕;confirmed_by 置空标记机器判定)
  - 5 ≤ score < 7 → 维持 suspected(LLM 中等置信,进人工复核队列)
  - score < 5 → 自动降为 watching(verdict='rejected',驳回样本作负例积累)
  - 终审不可用 → 跳过终审直进人工复核队列(status 维持 suspected),不自动告警

prompt 版本化：final-review-v1 注册进 PROMPT_REGISTRY(只增不改),与 T2.17
prompt 版本管理一致;每次终审写 llm_judgements(task_type='final_review':
模型名+prompt_version+输入/输出快照+耗时+成败)与 event.final_review
(score/verdict/model/prompt_version/reviewed_at)。

FinalReviewOutput 单一定义在 app.llm.schemas(prompt 注册表与本模块统一 import),
本模块仅 re-export 兼容既有引用。
"""
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.llm.prompts import TASK_FINAL_REVIEW, get_prompt
from app.llm.schemas import FinalReviewOutput, schema_instruction
from app.models.agenda import AgendaEvent
from app.models.llm import LLMJudgement

logger = get_logger("agenda.final_review")

VerdictType = Literal["completed", "rejected", "skipped_unavailable"]


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
    1. 若 event.status != 'suspected'：跳过重审（返回 verdict='skipped_unavailable' 无 score；
       未发起 LLM 调用，不写 llm_judgements）
    2. 调 llm_annotator.engine.generate_structured 走 final-review prompt（返回 (输出, 耗时秒)）
    3. 所有 LLM 调用路径（成功/失败/不可用）写 llm_judgements 留痕
       （task_type='final_review'，模型名+prompt_version+输入/输出快照+耗时+成败）
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
    model_name = llm_annotator.settings.resolved_model_name()

    def _record_judgement(
        *,
        success: bool,
        output_payload: dict | None,
        error: str | None,
        latency_ms: int,
    ) -> None:
        """写 llm_judgements 终审留痕（详细设计 3.2 关键不变量③）。"""
        db.add(LLMJudgement(
            topic_id=event.topic_id,
            task_type=TASK_FINAL_REVIEW,
            model_name=model_name,
            prompt_version=prompt_template.version,
            input_payload=input_payload,
            output_payload=output_payload,
            success=success,
            naming_method=None,  # 终审不涉及命名兜底链，置空（与命名/分类任务区分）
            error=error,
            latency_ms=latency_ms,
        ))
        db.flush()

    started = time.monotonic()
    try:
        output, latency_s = llm_annotator.engine.generate_structured(
            prompt_template.system,
            prompt_template.build_user(input_payload),
            FinalReviewOutput,
            max_retries=1,
        )
    except Exception as exc:  # noqa: BLE001 终审不可用不阻塞事件流
        latency_ms = int((time.monotonic() - started) * 1000)
        _record_judgement(success=False, output_payload=None, error=str(exc)[:300], latency_ms=latency_ms)
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

    latency_ms = int(latency_s * 1000)

    # 判定分支:score≥7 自动 confirmed(替代人工),5-6 维持 suspected 等人工,<5 降 watching
    passed = output.score >= 5 and output.verdict == "completed"
    auto_confirm = output.score >= 7 and output.verdict == "completed"
    verdict: VerdictType = "completed" if passed else "rejected"
    if auto_confirm:
        event.status = "confirmed"
        event.confidence = "confirmed"
        event.confirmed_at = now
        # confirmed_by 置空 = 机器自动确认(前端区分"LLM 确认" vs "人工确认")
        event.revision_log = (event.revision_log or []) + [{
            "at": now.isoformat(),
            "action": "auto_confirmed_by_llm",
            "score": output.score,
            "model": model_name,
            "reasoning": (output.reasoning or "")[:300],
        }]
    elif not passed:
        # 自动降疑似为 watching,不自动告警
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
    _record_judgement(
        success=True,
        output_payload={
            "score": output.score,
            "verdict": output.verdict,
            "reasoning": output.reasoning,
            "concerns": output.concerns,
        },
        error=None,
        latency_ms=latency_ms,
    )
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
    "FinalReviewOutput",  # re-export（唯一定义在 app.llm.schemas）兼容既有引用
    "FinalReviewResult",
    "review_event",
    "schema_instruction",  # re-export 供 prompt 模板使用
]
