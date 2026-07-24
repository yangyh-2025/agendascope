"""sources 业务：CRUD、覆盖率汇总、crawl-preview 试运行（US-02）、失败源人工重验证。"""
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import feedparser
import lxml.html
from sqlalchemy.orm import Session

from app.collector.discoverer import build_discoverer
from app.collector.fetcher import RequestsFetcher, build_fetcher
from app.collector.pipeline import CrawlPipeline
from app.collector.pipeline_extractor import extract_pipeline
from app.collector.types import FetchError
from app.core.errors import (
    CODE_CONFLICT,
    CODE_DATA_INSUFFICIENT,
    CODE_PARAM_INVALID,
    CODE_STATE_INVALID,
    BizError,
)
from app.core.logging import get_logger
from app.core.ssrf import validate_public_url
from app.models.collection import CollectionJob
from app.models.source import Source
from app.repositories.source_repo import SourceRepository
from app.schemas.source import SourceCreate, SourceUpdate

logger = get_logger("source_service")

_verify_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="source-verify")

COVERAGE_METHODOLOGY = "维基百科各国报纸列表 + ABYZ News Links + Media Cloud Directory + awesome-rss-feeds + Similarweb 流量排名取交集"

_PAYWALL_MARKERS = ("paywall", "subscribe to continue", "订阅后继续", "登录后阅读", "sign in to continue")


def country_confidence(share: float) -> str:
    if share >= 0.85:
        return "high"
    if share >= 0.70:
        return "medium"
    return "low"


class SourceService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = SourceRepository(db)

    # ---------- 查询 ----------

    def to_list_item(self, source: Source) -> dict:
        return {
            "id": str(source.id),
            "name": source.name,
            "name_zh": source.name_zh,
            "country_code": source.country_code,
            "media_type": source.media_type,
            "language": source.language,
            "collect_mode": source.collect_mode,
            "adapter_type": source.adapter_type,
            "poll_interval_min": source.poll_interval_min,
            "audience_weight": float(source.audience_weight) if source.audience_weight is not None else None,
            "coverage_confidence": source.coverage_confidence,
            "status": source.status,
            "is_custom": source.is_custom,
            "last_success_at": source.last_success_at.isoformat() if source.last_success_at else None,
            "health_24h": self.repo.health_24h(source.id),
        }

    def coverage_items(self) -> list[dict]:
        items = []
        for row in self.repo.coverage_by_country():
            share = row.pop("total_audience_share")
            row["total_audience_share"] = share
            row["coverage_confidence"] = country_confidence(share)
            row["coverage_gap"] = share < 0.70
            items.append(row)
        return items

    # ---------- 创建/更新 ----------

    def create(self, body: SourceCreate) -> Source:
        validate_public_url(body.homepage_url, resolve_dns=False)
        if body.feed_url:
            validate_public_url(body.feed_url, resolve_dns=False)
            existing = self.repo.get_by_feed_url(body.feed_url)
            if existing is not None:
                raise BizError(CODE_CONFLICT, "该 feed_url 已存在于源库", {"existing_source_id": str(existing.id)})
        if body.adapter_type == "pipeline" and not (body.crawl_config or {}).get("entry_points"):
            raise BizError(CODE_PARAM_INVALID, "adapter_type=pipeline 时 crawl_config.entry_points 必填")
        if body.collect_mode == "rsshub" and not (body.crawl_config or {}).get("rsshub_route"):
            raise BizError(CODE_PARAM_INVALID, "collect_mode=rsshub 时 crawl_config.rsshub_route 必填")
        source = Source(
            name=body.name,
            name_zh=body.name_zh,
            country_code=body.country_code,
            homepage_url=body.homepage_url,
            feed_url=body.feed_url,
            collect_mode=body.collect_mode,
            adapter_type=body.adapter_type,
            crawl_config=body.crawl_config or {},
            media_type=body.media_type,
            language=body.language,
            poll_interval_min=body.poll_interval_min,
            audience_weight=body.audience_weight,
            coverage_confidence=body.coverage_confidence,
        )
        self.db.add(source)
        self.db.flush()
        return source

    def update(self, source: Source, body: SourceUpdate) -> Source:
        data = body.model_dump(exclude_none=True)
        if "status" in data and data["status"] != source.status:
            # 非法流转拦截：failed→active 必须走 POST /sources/{id}/verify（详细设计 1.5）
            if source.status == "failed" and data["status"] == "active":
                raise BizError(CODE_STATE_INVALID, "failed 源需经 POST /sources/{id}/verify 验证后恢复")
            history = list(source.status_history or [])
            history.append({
                "from": source.status,
                "to": data["status"],
                "at": datetime.now(UTC).isoformat(),
                "reason": "管理员手工调整",
                "actor": "human",
            })
            source.status_history = history[-20:]
            if data["status"] == "failed":
                source.degraded_since = source.degraded_since or datetime.now(UTC)
        for key, value in data.items():
            setattr(source, key, value)
        self.db.flush()
        return source

    # ---------- 试运行预览（US-02） ----------

    def crawl_preview(self, url: str, adapter_type: str | None, crawl_config: dict | None) -> dict:
        started = time.monotonic()
        validate_public_url(url, resolve_dns=False)
        fetcher = RequestsFetcher()
        warnings: list[str] = []

        resolved_config: dict
        effective_adapter = adapter_type
        diagnostics: dict = {}

        if crawl_config:
            resolved_config = crawl_config
            effective_adapter = effective_adapter or "pipeline"
        else:
            # 自动判定：先探测原生 RSS（页面本身或 <link rel=alternate> 声明的 feed）
            feed_url = self._probe_feed(fetcher, url)
            if feed_url:
                effective_adapter = "rss"
                resolved_config = {
                    "fetcher": {"type": "requests"},
                    "discoverer": {"type": "rss"},
                    "extractor": {"type": "trafilatura"},
                    "entry_points": [feed_url],
                    "scroll_pages": 0,
                    "post_extra_action": None,
                    "proxy": None,
                }
            else:
                effective_adapter = "pipeline"
                resolved_config = {
                    "fetcher": {"type": "requests"},
                    "discoverer": {"type": "list_page"},
                    "extractor": {"type": "trafilatura"},
                    "entry_points": [url],
                    "scroll_pages": 0,
                    "post_extra_action": None,
                    "proxy": None,
                }

        try:
            items, diagnostics = self._discover(resolved_config, effective_adapter)
        except FetchError as exc:
            raise BizError(CODE_PARAM_INVALID, f"试运行抓取失败: {exc}") from exc

        samples = []
        pipeline_fetcher = build_fetcher(resolved_config)
        for item in items[:5]:
            sample = {"url": item.url, "title": item.title, "authors": item.authors,
                      "pub_time": item.pub_time.isoformat() if item.pub_time else None,
                      "content_len": 0, "ok": False}
            try:
                html, _ = pipeline_fetcher.fetch(item.url)
                result = extract_pipeline(html, item.url, item.title, item.summary,
                                          resolved_config.get("extractor") or {})
                sample["content_len"] = len(result.text)
                sample["ok"] = result.content_status == "full" and len(result.text) >= 10
                if not item.title and result.text:
                    sample["title"] = result.text.split("\n", 1)[0][:200]
                if result.content_status == "partial":
                    warnings.append(f"正文抽取降级为标题摘要: {item.url}")
            except FetchError as exc:
                warnings.append(f"样例抓取失败: {item.url} ({exc})")
            samples.append(sample)

        if any(s["content_len"] > 0 and s["content_len"] < 200 for s in samples):
            warnings.append("检测到疑似付费墙，仅可采集公开摘要")

        if not any(s["ok"] for s in samples):
            raise BizError(CODE_DATA_INSUFFICIENT, "试运行未提取到任何有效文章（content ≥10 字符），请调整 crawl_config 后重试")

        return {
            "adapter_type": effective_adapter,
            "resolved_config": resolved_config,
            "discovered": diagnostics,
            "samples": samples,
            "warnings": warnings,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    def _probe_feed(self, fetcher: RequestsFetcher, url: str) -> str | None:
        """探测原生 RSS：页面本身是 feed，或 HTML 中 <link type=application/rss+xml> 声明。"""
        try:
            content, _ = fetcher.fetch(url)
        except FetchError:
            return None
        if feedparser.parse(content).entries:
            return url
        try:
            doc = lxml.html.fromstring(content)
        except (ValueError, lxml.etree.ParserError):
            return None
        for link in doc.xpath("//link[@rel='alternate'][@type='application/rss+xml' or @type='application/atom+xml']"):
            href = link.get("href")
            if href:
                from urllib.parse import urljoin

                candidate = urljoin(url, href)
                try:
                    feed_content, _ = fetcher.fetch(candidate)
                    if feedparser.parse(feed_content).entries:
                        return candidate
                except FetchError:
                    continue
        return None

    def _discover(self, config: dict, adapter_type: str):
        if adapter_type == "rss":
            fetcher = RequestsFetcher()
            content, _ = fetcher.fetch(config["entry_points"][0])
            outcome = build_discoverer({"discoverer": {"type": "rss"}}).discover(content)
            return outcome.items, outcome.diagnostics
        pipeline = CrawlPipeline(config)
        if not pipeline.entry_points:
            raise BizError(CODE_PARAM_INVALID, "crawl_config.entry_points 必填")
        return pipeline.discover_items()


def run_verify_job(source_id, job_id) -> None:
    """后台执行失败源验证：试采集一轮，成功 → active 并留痕；失败 → 维持 failed。"""
    from app.collector.governance import Governance
    from app.collector.pipeline import PipelineCollector
    from app.collector.rss_collector import RssCollector
    from app.collector.submitter import Submitter
    from app.db.redis_client import get_cache_redis
    from app.db.session import get_session_factory

    db = get_session_factory()()
    try:
        gov = Governance(db, get_cache_redis())
        source = db.get(Source, source_id)
        job = db.get(CollectionJob, job_id)
        if source is None or job is None:
            return
        gov.mark_running(job)
        db.commit()
        try:
            if source.adapter_type == "pipeline":
                found, new = PipelineCollector(gov, Submitter()).run_round(source, job, max_articles=5)
            else:
                found, new = RssCollector(gov, Submitter()).run_round(source, job, max_articles=5)
            gov.mark_success(job, found, new)
            old = source.status
            source.status = "active"
            source.consecutive_failures = 0
            source.degraded_since = None
            source.last_success_at = datetime.now(UTC)
            history = list(source.status_history or [])
            history.append({
                "from": old, "to": "active",
                "at": datetime.now(UTC).isoformat(),
                "reason": f"人工重验证通过（试采集 {found} 篇）", "actor": "human",
            })
            source.status_history = history[-20:]
            logger.info("source_verify_pass", source_id=str(source.id), found=found)
        except Exception as exc:  # noqa: BLE001
            gov.mark_failure(job, f"验证采集失败: {exc}")
            logger.warning("source_verify_fail", source_id=str(source.id), error=str(exc))
        db.commit()
    finally:
        db.close()


def submit_verify_job(source: Source, job: CollectionJob) -> None:
    _verify_executor.submit(run_verify_job, source.id, job.id)
