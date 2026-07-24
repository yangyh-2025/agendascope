"""llm_service：本地 LLM 推理服务（T2.12–T2.17）。

本提交（T2.12）包含：双配置档 settings、transformers 推理引擎 engine、
JSON Schema 结构化输出 schemas、健康监控 health、异步批处理队列 queue。
命名/分类/摘要编排与降级链在后续提交接入。
"""
from app.llm.engine import LLMEngine
from app.llm.errors import LLMError, LLMParseError, LLMUnavailableError
from app.llm.health import DegradationMonitor
from app.llm.queue import LLMTaskQueue
from app.llm.settings import LLMSettings, get_llm_settings

__all__ = [
    "DegradationMonitor",
    "LLMEngine",
    "LLMError",
    "LLMParseError",
    "LLMSettings",
    "LLMTaskQueue",
    "LLMUnavailableError",
    "get_llm_settings",
]
