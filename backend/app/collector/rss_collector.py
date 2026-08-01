"""通用 RSS 采集器（T1.14，承载 ~80% 有官方 RSS 的源，估算）。

每源一行配置（sources.crawl_config JSONB，adapter_type='rss'），不是每源一个代码文件。
流程：feedparser 拉取 → 任务内/持久去重 → trafilatura→readability 正文抽取 → CollectedData 提交中枢。
"""
from datetime import UTC, datetime

import feedparser

from app.collector.extractor import extract_with_fallback
from app.collector.fetcher import RequestsFetcher, build_fetcher
from app.collector.governance import Governance, TaskUrlFilter
from app.collector.submitter import Submitter
from app.collector.types import CollectedData, FetchError
from app.collector.utils import url_hash
from app.core.logging import get_logger

logger = get_logger("rss_collector")


def _entry_pub_time(entry) -> datetime | None:
    struct = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if struct:
        return datetime(struct[0], struct[1], struct[2], struct[3], struct[4], struct[5], tzinfo=UTC)
    return None


def resolve_feed_url(source, settings=None) -> str | None:
    """按 collect_mode 解析实际拉取地址：

    - rss/gdelt：直接用 sources.feed_url
    - rsshub：RSSHUB_BASE + crawl_config.rsshub_route（RSSHub 自建实例将无原生 RSS 站点转为 feed）
    """
    if source.collect_mode == "rsshub":
        from app.config import get_settings

        settings = settings or get_settings()
        route = (source.crawl_config or {}).get("rsshub_route")
        if not route:
            return None
        return f"{settings.rsshub_base.rstrip('/')}/{route.lstrip('/')}"
    return source.feed_url


class RssCollector:
    """对单个 adapter_type='rss' 的源执行一轮采集。

    正文获取三级优先序：
    ① feed 自带全文（content:encoded，El País 等 MRSS 源，避免请求正文页触发反爬）
    ② 按 crawl_config.fetcher 配置抓取正文页（requests 或 playwright，France24/RFI 等反爬站点）
    ③ 仅存标题+摘要（partial，降级链留痕，绝不静默）
    """

    def __init__(self, governance: Governance, submitter: Submitter, fetcher: RequestsFetcher | None = None):
        self.gov = governance
        self.submitter = submitter
        self._fetcher_override = fetcher

    def _build_fetchers(self, source) -> tuple:
        """返回 (feed_fetcher, article_fetcher)：

        feed 本身是静态 XML，恒用 Requests 拉取；article_fetcher 才按 crawl_config.fetcher
        配置（requests/playwright）抓取正文页——反爬站点（France24/RFI 等）配置 playwright。
        """
        if self._fetcher_override is not None:
            return self._fetcher_override, self._fetcher_override
        article_fetcher = build_fetcher(source.crawl_config or {}, source.country_code)
        proxy_key = (source.crawl_config or {}).get("proxy")
        insecure_ssl = bool((source.crawl_config or {}).get("insecure_ssl"))
        return (
            RequestsFetcher(proxy_key=proxy_key, country_code=source.country_code, verify=not insecure_ssl),
            article_fetcher,
        )

    @staticmethod
    def _entry_fulltext_html(entry) -> str:
        """取 feed 条目自带全文 HTML（content:encoded / content）。"""
        for item in getattr(entry, "content", []) or []:
            value = item.get("value", "")
            if value and len(value) >= 100:
                return value
        return ""

    def run_round(self, source, job, max_articles: int = 50) -> tuple[int, int]:
        """执行一轮采集，返回 (articles_found, articles_new)。失败抛 FetchError 由调度器转治理状态机。"""
        feed_url = resolve_feed_url(source)
        if not feed_url:
            raise FetchError(f"{source.collect_mode} 源缺少可用 feed 地址（feed_url / rsshub_route）")

        feed_fetcher, article_fetcher = self._build_fetchers(source)
        self.submitter.resend_pending()  # 防重②：先重发上轮提交失败缓存

        feed_content, http_status = feed_fetcher.fetch(feed_url)
        parsed = feedparser.parse(feed_content)
        if parsed.bozo and not parsed.entries:
            raise FetchError(f"feed 解析失败: {parsed.bozo_exception}", http_status=http_status)

        informant = parsed.feed.get("title") or source.name
        task_filter = TaskUrlFilter()  # 防重③：任务内 URL 过滤
        found = 0
        new = 0
        latencies: list[float] = []

        for entry in parsed.entries[:max_articles]:
            link = getattr(entry, "link", "") or ""
            title = getattr(entry, "title", "") or ""
            if not link or not title:
                continue
            found += 1
            hash_hex = url_hash(link)
            if task_filter.seen(hash_hex):
                continue
            task_filter.add(hash_hex)
            if self.gov.is_duplicate(hash_hex):  # 防重①：持久去重
                continue

            summary = getattr(entry, "summary", "") or ""
            # ① feed 自带全文优先
            text = ""
            content_status = ""
            encoded = self._entry_fulltext_html(entry)
            if encoded:
                from app.collector.extractor import extract_content

                result = extract_content(encoded, link)
                if result.ok:
                    text = result.text
                    content_status = "full"
            # ② 抓正文页
            if not text:
                try:
                    html, _ = article_fetcher.fetch(link)
                    result = extract_with_fallback(html, link, title, summary)
                    text = result.text
                    content_status = result.content_status
                except FetchError as exc:
                    # 正文页抓取失败不阻断整轮：仅存标题+摘要（partial，绝不静默——method 记日志）
                    logger.warning("article_fetch_fail", url=link, error=str(exc))
                    fallback = extract_with_fallback("", link, title, summary)
                    text = fallback.text
                    content_status = fallback.content_status

            if len(text.strip()) < 10:
                continue  # 中枢会拒收（content <10 字符），本地直接跳过

            pub_time = _entry_pub_time(entry)
            if pub_time:
                latencies.append((datetime.now(UTC) - pub_time).total_seconds() / 60)

            submitted = self.submitter.submit(CollectedData(
                source_id=str(source.id),
                job_id=str(job.id),
                adapter_type="rss",
                url=link,
                title=title,
                content=text,
                informant=informant,
                authors=[a.get("name", "") for a in getattr(entry, "authors", []) if a.get("name")],
                pub_time=pub_time,
                content_status=content_status,
            ))
            if submitted:
                new += 1
                self.gov.record_fingerprint(hash_hex)

        job.latency_stats = {
            "published_min": min(latencies) if latencies else None,
            "published_max": max(latencies) if latencies else None,
            "avg_delay_min": round(sum(latencies) / len(latencies), 2) if latencies else None,
        }
        return found, new
