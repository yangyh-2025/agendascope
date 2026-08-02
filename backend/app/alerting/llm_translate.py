"""订阅日报 LLM 翻译（T4.19 增强：以 LLM 替代 argos 离线翻译）。

订阅日报/周报摘要原用 argos 离线翻译质量差。本模块改为调 LLM 把摘要
译成简体中文，质量更高、保留专有名词音译。

- 依赖注入 llm_annotator（复用 engine/monitor）；不可用/降级/LLMError →
  返回原文（不阻塞订阅发送，绝不静默失败）。
- llm_annotator 或 db 缺位时仍返回原文/译文，只是不写 llm_judgements 留痕。
- 走统一 generate_structured 信号量（LLM_MAX_CONCURRENCY=2 全局限流），不另开并发。
"""
from __future__ import annotations

from typing import Any

from app.llm import prompts
from app.llm.errors import LLMError
from app.llm.schemas import TranslateOutput
from app.models.llm import LLMJudgement


def llm_translate(
    db: Any,
    text: str,
    target_lang: str = "zh",
    llm_annotator: Any = None,
) -> str:
    """把给定文本（日报摘要）用 LLM 翻译为简体中文；不可用/降级/失败返回原文。

    Args:
        db: SQLAlchemy session（写 llm_judgements 留痕；None 时不写留痕）
        text: 待翻译文本
        target_lang: 目标语言（本实现仅支持 "zh" 简中；其他值直接返回原文）
        llm_annotator: TopicAnnotator 实例；None 或 monitor.degraded 时返回原文

    Returns:
        译文（成功）或原文（降级/不可用/LLM 失败）。
    """
    if not text:
        return text
    if target_lang != "zh":
        return text
    if llm_annotator is None or llm_annotator.monitor.degraded:
        return text

    input_payload = {"text": text, "target_lang": target_lang}
    template = prompts.get_prompt(prompts.TASK_TRANSLATE)
    user_prompt = template.build_user(input_payload)
    model_name = llm_annotator.engine.model_name

    def _record(success: bool, output: dict[str, Any] | None, error: str | None, latency_s: float) -> None:
        if db is None:
            return
        db.add(LLMJudgement(
            topic_id=None,
            task_type=prompts.TASK_TRANSLATE,
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
            template.system, user_prompt, TranslateOutput
        )
    except LLMError as exc:
        llm_annotator.monitor.record(False, reason=str(exc)[:200])
        _record(success=False, output=None, error=str(exc)[:300], latency_s=0.0)
        return text
    llm_annotator.monitor.record(True)
    _record(success=True, output={"translated": parsed.translated}, error=None, latency_s=latency_s)
    return parsed.translated


__all__ = ["llm_translate"]
