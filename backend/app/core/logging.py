"""结构化 JSON 日志（structlog）与 trace_id 上下文。

trace_id 贯穿 采集→抽取→NLP→入库→可见 全链路：
HTTP 入口由 TraceIdMiddleware 注入；collector 侧由任务上下文注入。

文件输出（T5.10）：配置 LOG_FILE_PATH 后，root logger 追加 RotatingFileHandler，
供管理后台日志查看与一键诊断包读取；留空保持仅 stdout。
"""
import logging
import sys
import uuid
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

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
    _attach_file_handler()
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


def _attach_file_handler() -> None:
    """按配置挂滚动文件 handler；重复调用不重复挂载（create_app 幂等）。"""
    from app.config import get_settings

    settings = get_settings()
    if not settings.log_file_path:
        return
    path = Path(settings.log_file_path)
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == path.absolute():
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=settings.log_file_max_mb * 1024 * 1024,
        backupCount=settings.log_file_backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)


def get_logger(name: str):
    return structlog.get_logger(name)
