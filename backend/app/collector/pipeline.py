"""配置驱动爬虫管线（T1.16，复刻 IIS IntelligenceCrawler 三段式设计）。

crawl_config JSONB 驱动，工厂按配置实例化：
  Fetcher(requests/playwright) → Discoverer(rss/sitemap/list_page) → Extractor(trafilatura/readability/generic_css)
服务于 adapter_type='pipeline' 的无 RSS 长尾源（~20%，估算）。与 RSS 路径共用治理状态机与 /internal/collect。
"""
from datetime import datetime, timezone

from app.collector.discoverer import ListPageDiscoverer, RSSDiscoverer, SitemapDiscoverer, build_discoverer
from app.collector.fetcher import build_fetcher
from app.collector.governance import Governance, TaskUrlFilter
from app.collector.pipeline_extractor import extract_pipeline
from app.collector.submitter import Submitter
from app.collector.types import CollectedData, FetchError
from app.collector.utils import url_hash
from app.core.logging import get_logger

logger = get_logger("pipeline")


class CrawlPipeline:
    """由 crawl_config 实例化的三段式管线（“配置即爬虫”）。"""

    def __init__(self, crawl_config: dict, country_code: str = ""):
        self.config = crawl_config or {}
        self.fetcher = build_fetcher(self.config, country_code)
        self.discoverer = build_discoverer(self.config)
        self.entry_points: list[str] = list(self.config.get("entry_points") or [])

    def discover_items(self):
        """对全部入口页执行发现，返回 (items, diagnostics)。"""
        all_items = []
        diagnostics = {}
        for entry_url in self.entry_points:
            content, _ = self.fetcher.fetch(entry_url)
            if isinstance(self.discoverer, (ListPageDiscoverer, RSSDiscoverer)):
                outcome = self.discoverer.discover(content, entry_url)
            elif isinstance(self.discoverer, SitemapDiscoverer):
                outcome = self.discoverer.discover(content, entry_url, fetcher=self.fetcher)
            else:
                outcome = self.discoverer.discover(content, entry_url)
            all_items.extend(outcome.items)
            diagnostics = {**diagnostics, **outcome.diagnostics}
        return all_items, diagnostics


class PipelineCollector:
    """对单个 adapter_type='pipeline' 的源执行一轮采集。"""

    def __init__(self, governance: Governance, submitter: Submitter):
        self.gov = governance
        self.submitter = submitter

    def run_round(self, source, job, max_articles: int = 50) -> tuple[int, int]:
        pipeline = CrawlPipeline(source.crawl_config, country_code=source.country_code)
        if not pipeline.entry_points:
            raise FetchError("pipeline 源缺少 crawl_config.entry_points")

        self.submitter.resend_pending()  # 防重②

        items, _ = pipeline.discover_items()
        task_filter = TaskUrlFilter()  # 防重③
        found = 0
        new = 0

        for item in items[:max_articles]:
            if not item.url:
                continue
            found += 1
            hash_hex = url_hash(item.url)
            if task_filter.seen(hash_hex):
                continue
            task_filter.add(hash_hex)
            if self.gov.is_duplicate(hash_hex):  # 防重①
                continue

            try:
                html, _ = pipeline.fetcher.fetch(item.url)
            except FetchError as exc:
                logger.warning("pipeline_article_fetch_fail", url=item.url, error=str(exc))
                continue

            result = extract_pipeline(
                html, item.url, item.title, item.summary,
                (source.crawl_config or {}).get("extractor") or {},
            )
            title = item.title or result.text.split("\n", 1)[0][:200]
            if not title or len(result.text.strip()) < 10:
                continue

            submitted = self.submitter.submit(CollectedData(
                source_id=str(source.id),
                job_id=str(job.id),
                adapter_type="pipeline",
                url=item.url,
                title=title,
                content=result.text,
                informant=source.name,
                authors=item.authors,
                pub_time=item.pub_time,
                content_status=result.content_status,
            ))
            if submitted:
                new += 1
                self.gov.record_fingerprint(hash_hex)

        return found, new
