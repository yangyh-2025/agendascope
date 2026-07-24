"""统一响应结构 {code, data, message} 与错误码体系（详细设计 1.1/1.2）。"""
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# 错误码表（详细设计 §1.1）
CODE_OK = 0
CODE_PARAM_INVALID = 1001        # 参数校验失败
CODE_URL_INVALID = 1002          # 非法 URL / SSRF 拦截
CODE_UNAUTHORIZED = 2001         # 未认证 / token 非法
CODE_FORBIDDEN = 2002            # 无权限
CODE_BAD_CREDENTIALS = 2003      # 凭据错误
CODE_ACCOUNT_DISABLED = 2004     # 账号被禁用
CODE_NOT_FOUND = 3001            # 资源不存在
CODE_CONFLICT = 4001             # 资源冲突（唯一约束）
CODE_STATE_INVALID = 4002        # 业务规则限制（状态机非法流转）
CODE_QUOTA_EXCEEDED = 4003       # 配额超限
CODE_DATA_INSUFFICIENT = 4004    # 数据不足
CODE_RATE_LIMITED = 5001         # 请求过于频繁
CODE_INTERNAL_ERROR = 9001       # 服务器内部错误
CODE_DEPENDENCY_DEGRADED = 9002  # 依赖服务降级

_HTTP_BY_CODE = {
    CODE_OK: 200,
    CODE_PARAM_INVALID: 400,
    CODE_URL_INVALID: 400,
    CODE_UNAUTHORIZED: 401,
    CODE_BAD_CREDENTIALS: 401,
    CODE_FORBIDDEN: 403,
    CODE_ACCOUNT_DISABLED: 403,
    CODE_NOT_FOUND: 404,
    CODE_CONFLICT: 409,
    CODE_STATE_INVALID: 422,
    CODE_QUOTA_EXCEEDED: 422,
    CODE_DATA_INSUFFICIENT: 422,
    CODE_RATE_LIMITED: 429,
    CODE_INTERNAL_ERROR: 500,
    CODE_DEPENDENCY_DEGRADED: 503,
}


def ok(data: Any = None, message: str = "ok") -> dict:
    return {"code": CODE_OK, "data": data, "message": message}


class BizError(Exception):
    """业务异常：携带错误码与用户可读消息，由全局异常处理器转为统一响应。"""

    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


def _body(code: int, data: Any, message: str) -> dict:
    return {"code": code, "data": data, "message": message}


def register_exception_handlers(app) -> None:
    @app.exception_handler(BizError)
    async def biz_error_handler(request: Request, exc: BizError):
        return JSONResponse(
            status_code=_HTTP_BY_CODE.get(exc.code, 500),
            content=_body(exc.code, exc.data, exc.message),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        fields: dict[str, str] = {}
        for err in exc.errors():
            loc = [str(x) for x in err.get("loc", []) if x not in ("body", "query", "path")]
            fields[".".join(loc) or "body"] = err.get("msg", "参数非法")
        return JSONResponse(
            status_code=400,
            content=_body(CODE_PARAM_INVALID, {"fields": fields}, "参数校验失败"),
        )

    @app.exception_handler(Exception)
    async def unknown_error_handler(request: Request, exc: Exception):
        from app.core.logging import get_logger

        get_logger("api").error("unhandled_exception", path=str(request.url.path), exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=_body(CODE_INTERNAL_ERROR, None, "服务器内部错误"),
        )
