"""采集端共享类型：CollectedData（对齐详细设计 1.13 载荷）。"""
import uuid as uuid_mod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class CollectedData:
    source_id: str
    url: str
    title: str
    content: str
    informant: str
    adapter_type: str                 # rss / pipeline
    job_id: str | None = None
    authors: list[str] = field(default_factory=list)
    pub_time: datetime | None = None
    content_status: str = "full"      # full / partial（中枢落库 articles.content_status）
    uuid: str = field(default_factory=lambda: str(uuid_mod.uuid4()))

    def to_payload(self) -> dict:
        return {
            "uuid": self.uuid,
            "source_id": self.source_id,
            "job_id": self.job_id,
            "adapter_type": self.adapter_type,
            "url": self.url,
            "title": self.title,
            "content": self.content,
            "informant": self.informant,
            "authors": self.authors,
            "pub_time": self.pub_time.isoformat() if self.pub_time else None,
            "content_status": self.content_status,
        }


@dataclass
class DiscoveredItem:
    url: str
    title: str = ""
    summary: str = ""
    pub_time: datetime | None = None
    authors: list[str] = field(default_factory=list)


class FetchError(Exception):
    def __init__(self, message: str, http_status: int | None = None):
        self.http_status = http_status
        super().__init__(message)
