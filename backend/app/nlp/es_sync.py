"""Elasticsearch 8 全文索引同步（T2.4）。

标题/正文同步至 ES 检索副本，PG 为唯一事实源、ES 最终一致：
- doc _id = article_id，index 操作幂等 upsert，重投递不产生脏副本
- 多语言 analyzer：基础字段 standard analyzer + 按文章语言写入 title_<lang>/content_<lang>
  语言专属字段（dynamic_templates 按字段名后缀挂内置语言 analyzer，覆盖监控国主要语种）
- 失败重试有界：指数退避 ≤ es_max_retries 次（默认总耗时 ~31s，不死等），
  超限抛 EsSyncError 由 worker 整批重投递；PG 侧语言/向量已落库不受影响
"""
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from elasticsearch import Elasticsearch
from elasticsearch.exceptions import ApiError, ConnectionTimeout
from elasticsearch.exceptions import ConnectionError as EsConnectionError

from app.config import get_settings
from app.core.logging import get_logger
from app.nlp.config import get_nlp_settings

logger = get_logger("nlp.es_sync")

# 语言 → ES 内置 analyzer（覆盖监控国主要语种；其余语种落基础字段走 standard）
_LANG_ANALYZERS = {
    "en": "english", "zh": "cjk", "ja": "cjk", "ko": "cjk",
    "ar": "arabic", "ru": "russian", "de": "german", "fr": "french",
    "es": "spanish", "pt": "portuguese", "tr": "turkish", "fa": "persian",
}

_CONTENT_MAX_CHARS = 20000  # 索引副本截断上限（展示层只出 ≤150 字摘录，长文不整篇入索引）


class EsSyncError(Exception):
    """ES 同步最终失败（重试耗尽或不可重试错误），由上层决定重投递/死信。"""


@dataclass(frozen=True)
class ArticleDoc:
    article_id: UUID
    title: str
    content: str | None
    summary: str | None
    language: str
    country_code: str
    source_id: UUID
    source_channel: str
    published_at: datetime

    def to_source(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "title": self.title,
            "content": (self.content or "")[:_CONTENT_MAX_CHARS],
            "summary": self.summary,
            "language": self.language,
            "country_code": self.country_code,
            "source_id": str(self.source_id),
            "source_channel": self.source_channel,
            "published_at": self.published_at.isoformat(),
        }
        analyzer = _LANG_ANALYZERS.get(self.language)
        if analyzer:  # 语言专属字段，多语言 analyzer 按字段名后缀挂载
            doc[f"title_{self.language}"] = self.title
            doc[f"content_{self.language}"] = doc["content"]
        return doc


def _index_mapping() -> dict[str, Any]:
    lang_templates = [
        {
            f"lang_{lang}": {
                "path_match": f"*_{lang}",
                "match_mapping_type": "string",
                "mapping": {"type": "text", "analyzer": analyzer},
            }
        }
        for lang, analyzer in _LANG_ANALYZERS.items()
    ]
    return {
        "dynamic_templates": lang_templates,
        "properties": {
            "title": {"type": "text", "analyzer": "standard"},
            "content": {"type": "text", "analyzer": "standard"},
            "summary": {"type": "text", "analyzer": "standard"},
            "language": {"type": "keyword"},
            "country_code": {"type": "keyword"},
            "source_id": {"type": "keyword"},
            "source_channel": {"type": "keyword"},
            "url": {"type": "keyword", "index": False},
            "published_at": {"type": "date"},
        },
    }


class EsArticleIndexer:
    def __init__(
        self,
        url: str | None = None,
        index: str | None = None,
        max_retries: int | None = None,
        backoff: float | None = None,
    ):
        settings = get_nlp_settings()
        self.index = index or settings.es_index
        self.url = url or settings.es_url or get_settings().elasticsearch_url
        self.max_retries = max_retries if max_retries is not None else settings.es_max_retries
        self.backoff = backoff if backoff is not None else settings.es_retry_backoff_seconds
        self.client = Elasticsearch(self.url, request_timeout=30)

    def ensure_index(self) -> None:
        if not self.client.indices.exists(index=self.index):
            self.client.indices.create(index=self.index, mappings=_index_mapping())
            logger.info("es_index_created", index=self.index)

    def _with_retry(self, fn, action: str):
        """有界指数退避：1→2→4→8→16s 封顶（默认 5 次，总耗时 ≤31s），不死等。"""
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return fn()
            except (EsConnectionError, ConnectionTimeout) as exc:
                last_exc = exc
            except ApiError as exc:
                last_exc = exc
                if exc.status_code not in (429, 500, 502, 503, 504):  # 4xx 重试无意义
                    raise EsSyncError(f"ES {action} 不可重试错误: {exc.status_code} {exc.message}") from exc
            if attempt < self.max_retries:
                delay = min(self.backoff * (2**attempt), 16.0)
                logger.warning("es_retry", action=action, attempt=attempt + 1, delay=delay, error=str(last_exc)[:200])
                time.sleep(delay)
        raise EsSyncError(f"ES {action} 重试 {self.max_retries} 次仍失败: {last_exc}") from last_exc

    def index_articles(self, docs: list[ArticleDoc]) -> int:
        """bulk 幂等写入（_id=article_id upsert），返回成功数；失败有界重试后抛 EsSyncError。"""
        if not docs:
            return 0
        from elasticsearch.helpers import bulk

        self._with_retry(self.ensure_index, "ensure_index")
        actions = [{"_index": self.index, "_id": str(doc.article_id), "_source": doc.to_source()} for doc in docs]

        def call() -> int:
            succeeded, errors = bulk(self.client, actions, raise_on_error=False)
            error_list = errors if isinstance(errors, list) else []
            if error_list:
                retryable = [e for e in error_list if e.get("index", {}).get("status") in (429, 500, 502, 503, 504)]
                if retryable:
                    raise EsConnectionError(f"bulk 部分失败(可重试): {len(retryable)} 条")
                raise EsSyncError(f"bulk 部分失败(不可重试): {error_list[0]}")
            return int(succeeded)

        count = self._with_retry(call, "index_articles")
        logger.info("es_indexed", index=self.index, count=count)
        return count
