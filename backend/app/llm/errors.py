"""LLM 服务异常类型。"""


class LLMError(Exception):
    """LLM 服务基础异常。"""


class LLMUnavailableError(LLMError):
    """模型未加载 / 推理超时 / 服务崩溃 → 触发降级链。"""


class LLMParseError(LLMError):
    """结构化输出解析/校验失败（重试 1 次后仍失败）→ 单点降级。"""
