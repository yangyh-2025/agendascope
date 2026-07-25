"""agenda_events 相关请求/响应模型（详细设计 1.8）。"""
from pydantic import BaseModel, Field


class DismissEventRequest(BaseModel):
    """POST /agenda-events/{id}/dismiss 请求体（详细设计 1.8）。"""

    reason: str = Field(min_length=1, max_length=500, description="排除原因（必填，≤500 字）")
    false_positive: bool = Field(default=False, description="是否计入误报率评估（PRD 8.4）")


__all__ = ["DismissEventRequest"]
