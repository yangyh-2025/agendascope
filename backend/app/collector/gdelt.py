"""GDELT 兜底采集器（T1.19 + 交付项 6）：定时拉取 GDELT DOC 2.0 API 最新文章。

- 免 key，15 min 轮询节奏（与调度器 gdelt_interval_seconds 对齐）
- 按国家过滤（sourcecountry）+ 与自有采集按 URL 去重合并（同一治理状态机防重①）
- 域名匹配已登记源则归属该源，否则挂靠"GDELT 兜底通道"伪源
- 正文经同一抽取降级链获取；抽取失败的条目跳过并记日志（不伪造内容）
- 统一走 POST /internal/collect 通道入库
"""
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector.extractor import extract_with_fallback
from app.collector.fetcher import RequestsFetcher
from app.collector.gdelt_buffer import GdeltBuffer
from app.collector.governance import Governance, TaskUrlFilter
from app.collector.submitter import Submitter
from app.collector.types import CollectedData
from app.collector.utils import url_hash
from app.config import get_settings
from app.core.logging import get_logger
from app.models.source import Source
from app.services.seed_service import ensure_gdelt_pseudo_source

logger = get_logger("gdelt")


def parse_seen_date(raw: str) -> datetime | None:
    """GDELT seendate 形如 20260724T053000Z。"""
    try:
        return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


class GdeltCollector:
    def __init__(self, db: Session, governance: Governance, submitter: Submitter,
                 fetcher: RequestsFetcher | None = None):
        self.db = db
        self.gov = governance
        self.submitter = submitter
        self.fetcher = fetcher or RequestsFetcher()
        self.settings = get_settings()

    def build_query(self) -> str:
        # GDELT DOC 2.0 的 sourcecountry 操作符接受两位国家码（FIPS 10-4，与常用 ISO 码一致）
        countries = [c.strip() for c in self.settings.gdelt_countries.split(",") if c.strip()]
        clauses = [f"sourcecountry:{c}" for c in countries]
        return f"({' OR '.join(clauses)})" if clauses else "breaking"

    def fetch_latest(self, timespan: str = "15min") -> list[dict]:
        params = {
            "query": self.build_query(),
            "mode": "ArtList",
            "maxrecords": str(self.settings.gdelt_max_records),
            "format": "json",
            "timespan": timespan,
        }
        resp = requests.get(self.settings.gdelt_api_base, params=params, timeout=self.settings.crawl_timeout_seconds)
        resp.raise_for_status()
        data = resp.json()
        return data.get("articles") or []

    def fetch_with_buffer_fallback(self, buffer: GdeltBuffer, timespan: str = "15min") -> list[dict]:
        """先走 DOC API；成功则刷新 15 分钟批 CSV 本地缓冲；故障降级读最近缓冲（T1.19）。

        缓冲也为空时向上抛异常，由调度器记治理状态（绝不静默降级）。
        """
        try:
            articles = self.fetch_latest(timespan)
        except Exception as exc:  # noqa: BLE001 429/超时/5xx/连接失败/解析失败统一降级
            logger.warning("gdelt_api_fail_fallback_buffer", error=str(exc)[:200])
            return buffer.read_latest()
        buffer.save_articles(articles)
        return articles

    def _resolve_source(self, domain: str) -> Source | None:
        """按域名匹配已登记源（自有采集源优先）；未命中挂靠 GDELT 伪源。"""
        if domain:
            sources = self.db.scalars(select(Source).where(Source.collect_mode != "gdelt")).all()
            for source in sources:
                for candidate in (source.homepage_url, source.feed_url):
                    if not candidate:
                        continue
                    host = (urlparse(candidate).hostname or "").lower()
                    if host and (domain == host or domain.endswith("." + host) or host.endswith("." + domain)):
                        return source
        return ensure_gdelt_pseudo_source(self.db)

    def run_round(self, job=None) -> tuple[int, int]:
        """执行一轮 GDELT 拉取，返回 (found, new)。缓冲也无效时异常向上抛由调用方记治理状态。"""
        self.submitter.resend_pending()
        buffer = GdeltBuffer(self.settings.gdelt_buffer_dir)
        articles = self.fetch_with_buffer_fallback(buffer)
        task_filter = TaskUrlFilter()
        found = 0
        new = 0

        for item in articles:
            url = (item.get("url") or "").strip()
            title = (item.get("title") or "").strip()
            if not url or not title:
                continue
            found += 1
            hash_hex = url_hash(url)
            if task_filter.seen(hash_hex):
                continue
            task_filter.add(hash_hex)
            if self.gov.is_duplicate(hash_hex):  # 与自有采集按 URL 去重合并
                continue

            try:
                html, _ = self.fetcher.fetch(url)
                result = extract_with_fallback(html, url, title, "")
            except Exception as exc:  # noqa: BLE001 单篇失败跳过，不阻断整轮
                logger.warning("gdelt_article_extract_fail", url=url, error=str(exc))
                continue
            if not result.ok or len(result.text.strip()) < 10:
                continue  # 中枢拒收 content <10，且不允许伪造内容

            domain = (item.get("domain") or urlparse(url).hostname or "").lower()
            source = self._resolve_source(domain)
            submitted = self.submitter.submit(CollectedData(
                source_id=str(source.id),
                job_id=str(job.id) if job else None,
                adapter_type="rss",
                url=url,
                title=title,
                content=result.text,
                informant=f"GDELT/{domain or 'unknown'}",
                pub_time=parse_seen_date(item.get("seendate", "")),
                content_status=result.content_status,
            ))
            if submitted:
                new += 1
                self.gov.record_fingerprint(hash_hex)

        logger.info("gdelt_round_done", found=found, new=new)
        return found, new
