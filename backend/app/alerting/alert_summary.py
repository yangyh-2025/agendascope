"""告警理由 LLM 摘要（T4.14 增强：预警规则命中触发时生成中文理由摘要）。

规则匹配命中（写入 alerts 表）时，调 LLM 根据命中规则与相关报道标题生成
一条中文告警理由摘要（≤200 字），写入 alert payload["summary"] 供前端
预警卡片展示与用户快速定位触发原因。

- 依赖注入 llm_annotator（复用 engine/monitor）；None 或 monitor.degraded
  时返回 None，调用方维持现状（不写 summary，行为与现状等价，向后兼容）。
- 每次判定（含失败）写 llm_judgements 留痕（详细设计 3.2 关键不变量③）。
- 走统一 generate_structured 信号量（LLM_MAX_CONCURRENCY=2 全局限流），不另开并发。
"""
from __future__ import annotations

from typing import Any

from app.llm import prompts
from app.llm.errors import LLMError
from app.llm.schemas import AlertSummaryOutput
from app.models.llm import LLMJudgement

MAX_ARTICLES = 5  # 输入预算：相关报道标题条数上限


def generate_alert_summary(
    db: Any,
    alert_context: dict[str, Any],
    llm_annotator: Any = None,
) -> str | None:
    """根据命中规则与相关报道生成中文告警理由摘要。

    Args:
        db: SQLAlchemy session（写 llm_judgements 留痕）
        alert_context: 告警上下文，含 rule_name / rule_conditions / matched_articles(≤5) / country_code
        llm_annotator: TopicAnnotator 实例；None 或 monitor.degraded 时返回 None

    Returns:
        中文摘要字符串（成功）或 None（降级/不可用/LLM 失败）。
    """
    if llm_annotator is None or llm_annotator.monitor.degraded:
        return None

    input_payload = _build_payload(alert_context)
    template = prompts.get_prompt(prompts.TASK_ALERT_SUMMARY)
    user_prompt = template.build_user(input_payload)
    model_name = llm_annotator.engine.model_name

    def _record(success: bool, output: dict[str, Any] | None, error: str | None, latency_s: float) -> None:
        db.add(LLMJudgement(
            topic_id=None,  # 告警摘要不绑定单一议题
            task_type=prompts.TASK_ALERT_SUMMARY,
            model_name=model_name,
            prompt_version=template.version,
            input_payload=input_payload,
            output_payload=output,
            success=success,
            naming_method=None,
            error=error,
            latency_ms=int(latency_s * 1000),
        ))
        db.flush()

    if not llm_annotator.engine.is_loaded:
        llm_annotator.engine.load()
    try:
        parsed, latency_s = llm_annotator.engine.generate_structured(
            template.system, user_prompt, AlertSummaryOutput
        )
    except LLMError as exc:
        llm_annotator.monitor.record(False, reason=str(exc)[:200])
        _record(success=False, output=None, error=str(exc)[:300], latency_s=0.0)
        return None
    llm_annotator.monitor.record(True)
    _record(success=True, output={"summary": parsed.summary}, error=None, latency_s=latency_s)
    return parsed.summary


def _build_payload(alert_context: dict[str, Any]) -> dict[str, Any]:
    """裁剪输入：matched_articles 标题取前 MAX_ARTICLES 条。"""
    articles = alert_context.get("matched_articles") or []
    return {
        "rule_name": alert_context.get("rule_name") or "",
        "rule_conditions": alert_context.get("rule_conditions") or {},
        "matched_articles": [str(a) for a in articles[:MAX_ARTICLES]],
        "country_code": alert_context.get("country_code") or "",
    }


__all__ = ["generate_alert_summary"]
