"""llm_service：本地 LLM 推理服务（T2.12–T2.17）。

模块划分：
- settings   双配置档（gpu-24g / cpu-quant / cpu-dev）与运行参数
- engine     transformers 本地推理引擎（Qwen 系列，真实模型权重，无 Mock）
- prompts    prompt 模板与版本注册表（命名/分类/摘要，few-shot 对照）
- schemas    结构化输出定义（JSON Schema 强约束 + pydantic 校验）
- ctfidf     LLM 降级时的 c-TF-IDF 关键词标签兜底
- health     推理健康监控（超时/失败率 >20% → 降级判定）
- queue      异步批处理队列（不阻塞采集主链路）
- alerting   LLM 降级 P1 告警写入 alerts 表
- annotator  议题命名/分类/摘要编排 + 落库留痕 + 降级回填 + 版本重跑对比
"""
from app.llm.annotator import TopicAnnotator
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
    "TopicAnnotator",
    "get_llm_settings",
]
