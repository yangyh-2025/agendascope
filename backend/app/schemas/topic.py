"""topics 相关请求/响应模型（详细设计 1.7）。

仅声明需要在路由间复用的结构；列表/详情字段过多，直接 dict 拼装保持灵活性。
"""
from pydantic import BaseModel, Field


class TopicRenameRequest(BaseModel):
    """PUT /topics/{id} 人工重命名/改分类（详细设计 1.7）。"""

    name: str | None = Field(default=None, max_length=300, description="人工命名（可选）")
    topic_category: str | None = Field(
        default=None,
        max_length=50,
        description="议题分类（政治安全/经济金融/军事/科技/能源气候/社会民生/其他，可选）",
    )


__all__ = ["TopicRenameRequest"]
