"""CollectedData 载荷模型（详细设计 1.13）。"""
import uuid as uuid_mod
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class CollectedPayload(BaseModel):
    uuid: uuid_mod.UUID
    source_id: uuid_mod.UUID
    job_id: uuid_mod.UUID | None = None
    adapter_type: str = Field(pattern="^(rss|pipeline)$")
    url: str = Field(min_length=1, max_length=1000)
    title: str = Field(min_length=1)
    content: str = Field(min_length=10)  # 不足 10 字符拒收（对齐 IIS CollectedData 校验）
    informant: str = Field(min_length=1)
    authors: list[str] = Field(default_factory=list)
    pub_time: datetime | None = None
    content_status: str = Field(default="full", pattern="^(full|partial|failed)$")
    time_source: str | None = Field(default=None, pattern="^(feed|crawled|gdelt)$")

    @field_validator("url")
    @classmethod
    def url_scheme(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("url 仅允许 http/https")
        return v
