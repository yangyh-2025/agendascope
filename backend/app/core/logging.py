"""结构化 JSON 日志（structlog）与 trace_id 上下文。

trace_id 贯穿 采集→抽取→NLP→入库→可见 全链路：
HTTP 入口由 TraceIdMiddleware 注入；collector 侧由任务上下文注入。
"""
import logging
import sys
import uuid
from contextvars import ContextVar

import structlog

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")


def new_trace_id() -> str:
    return str(uuid.uuid4())


def get_trace_id() -> str:
    return trace_id_var.get() or ""


def set_trace_id(trace_id: str) -> None:
    trace_id_var.set(trace_id)


def _inject_trace_id(logger, method_name, event_dict):
    tid = trace_id_var.get()
    if tid:
        event_dict["trace_id"] = tid
    return event_dict


def configure_logging(debug: bool = False) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.DEBUG if debug else logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            _inject_trace_id,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str):
    return structlog.get_logger(name)
