"""sources 模块请求模型（详细设计 1.5）。"""
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")


class CrawlConfig(BaseModel):
    fetcher: dict = Field(default_factory=lambda: {"type": "requests"})
    discoverer: dict = Field(default_factory=lambda: {"type": "list_page"})
    extractor: dict = Field(default_factory=lambda: {"type": "trafilatura"})
    entry_points: list[str] = Field(default_factory=list)
    scroll_pages: int = Field(default=0, ge=0, le=20)
    post_extra_action: dict | None = None
    proxy: str | None = Field(default=None, pattern="^(global_site_proxy|cn_site_proxy)$")


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    name_zh: str | None = Field(default=None, max_length=200)
    country_code: str
    homepage_url: str = Field(min_length=1, max_length=500)
    feed_url: str | None = Field(default=None, max_length=500)
    collect_mode: Literal["rss", "rsshub", "gdelt"] = "rss"
    adapter_type: Literal["rss", "pipeline"] = "rss"
    crawl_config: dict | None = None
    media_type: Literal["newspaper", "agency", "broadcast", "online"]
    language: str = Field(min_length=2, max_length=10)
    poll_interval_min: int = Field(default=5, ge=1, le=60)
    audience_weight: float | None = Field(default=None, ge=0, le=100)
    coverage_confidence: Literal["high", "medium", "low"] = "medium"

    @field_validator("country_code")
    @classmethod
    def country_format(cls, v: str) -> str:
        if not _COUNTRY_RE.match(v):
            raise ValueError("country_code 须为 ISO 3166-1 alpha-2 大写两位码")
        return v

    @field_validator("homepage_url", "feed_url")
    @classmethod
    def url_scheme(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith(("http://", "https://")):
            raise ValueError("仅允许 http/https URL")
        return v


class SourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    name_zh: str | None = Field(default=None, max_length=200)
    feed_url: str | None = Field(default=None, max_length=500)
    collect_mode: Literal["rss", "rsshub", "gdelt"] | None = None
    adapter_type: Literal["rss", "pipeline"] | None = None
    crawl_config: dict | None = None
    media_type: Literal["newspaper", "agency", "broadcast", "online"] | None = None
    language: str | None = Field(default=None, min_length=2, max_length=10)
    poll_interval_min: int | None = Field(default=None, ge=1, le=60)
    audience_weight: float | None = Field(default=None, ge=0, le=100)
    coverage_confidence: Literal["high", "medium", "low"] | None = None
    status: Literal["active", "degraded", "failed"] | None = None


class CrawlPreviewRequest(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    adapter_type: Literal["rss", "pipeline"] | None = None
    crawl_config: dict | None = None
