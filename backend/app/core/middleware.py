"""trace_id 中间件：每请求注入/透传 trace_id（X-Trace-Id 头可续传）。"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.logging import new_trace_id, set_trace_id


class TraceIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("x-trace-id") or new_trace_id()
        set_trace_id(trace_id)
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response
