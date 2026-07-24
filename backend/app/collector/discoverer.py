"""Discoverer 层（三段式之第二段）：RSS / Sitemap / ListPage 链接签名聚类。

ListPageDiscoverer 实现详细设计算法 6：对列表页所有 <a> 生成结构化路径签名
（向上 5 层、剔除噪音类、数字泛化），签名聚类取 ≥5 链接最大簇为文章列表，免手写 selector。
复刻 IIS IntelligenceCrawler 设计（子模块无 LICENSE，复刻思路不搬代码）。
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import feedparser
import lxml.html

from app.collector.types import DiscoveredItem, FetchError

PATH_DEPTH = 5
MIN_CLUSTER = 5

# 状态/噪音类与工具类（详细设计算法 6：剔除 odd/even/active 等状态类与图标/纯样式类）
_NOISE_CLASS = re.compile(
    r"^(odd|even|active|hover|selected|current|first|last|disabled|hidden|show|open|"
    r"icon|fa|glyphicon|bi|material-icons|clearfix|sr-only|visually-hidden)$",
    re.IGNORECASE,
)
_NOISE_CLASS_PREFIX = re.compile(r"^(fa-|icon-|glyphicon-|bi-|mdi-|material-)", re.IGNORECASE)
_DIGITS = re.compile(r"\d+")


def _clean_classes(class_attr: str) -> list[str]:
    cleaned = []
    for cls in (class_attr or "").split():
        if _NOISE_CLASS.match(cls) or _NOISE_CLASS_PREFIX.match(cls):
            continue
        cls = _DIGITS.sub("N", cls)  # 数字泛化 item-123 → item-N
        cleaned.append(cls)
    return sorted(cleaned)


def path_signature(element) -> str:
    """生成元素的结构化路径签名：自身 + 向上 PATH_DEPTH 层祖先，每层 tag+清洗后 class。"""
    parts = []
    node = element
    for _ in range(PATH_DEPTH + 1):
        if node is None or not isinstance(node.tag, str):
            break
        classes = _clean_classes(node.get("class", ""))
        tag = node.tag.lower()
        parts.append(f"{tag}.{'.'.join(classes)}" if classes else tag)
        node = node.getparent()
    return ">".join(reversed(parts))


@dataclass
class DiscoverOutcome:
    items: list[DiscoveredItem]
    diagnostics: dict = field(default_factory=dict)


class ListPageDiscoverer:
    """列表页链接签名聚类发现器（算法 6）。"""

    type = "list_page"

    def __init__(self, min_cluster: int = MIN_CLUSTER, path_depth: int = PATH_DEPTH):
        self.min_cluster = min_cluster
        self.path_depth = path_depth

    def discover(self, html: str, base_url: str) -> DiscoverOutcome:
        doc = lxml.html.fromstring(html)
        base_host = urlparse(base_url).hostname or ""
        signature_map: dict[str, dict[str, str]] = {}  # signature -> {abs_url: path}
        links_total = 0

        for anchor in doc.iter("a"):
            href = anchor.get("href")
            if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
                continue
            abs_url = urljoin(base_url, href.split("#")[0])
            parsed = urlparse(abs_url)
            if parsed.scheme not in ("http", "https") or parsed.hostname != base_host:
                continue  # 仅保留同站链接
            links_total += 1
            signature = path_signature(anchor)
            signature_map.setdefault(signature, {})[abs_url] = signature

        if not signature_map:
            return DiscoverOutcome(items=[], diagnostics={"links_total": links_total, "article_cluster_size": 0, "signature": None})

        # 取链接数 ≥ min_cluster 的最大簇为文章列表
        eligible = [(sig, urls) for sig, urls in signature_map.items() if len(urls) >= self.min_cluster]
        if not eligible:
            return DiscoverOutcome(
                items=[],
                diagnostics={
                    "links_total": links_total,
                    "article_cluster_size": max(len(u) for u in signature_map.values()),
                    "signature": None,
                },
            )
        best_sig, best_urls = max(eligible, key=lambda kv: len(kv[1]))
        items = [DiscoveredItem(url=u) for u in sorted(best_urls)]
        return DiscoverOutcome(
            items=items,
            diagnostics={
                "links_total": links_total,
                "article_cluster_size": len(best_urls),
                "signature": best_sig,
            },
        )


class RSSDiscoverer:
    """以 RSS/Atom feed 为发现源（pipeline 内复用，entry_points 为 feed URL）。"""

    type = "rss"

    def discover(self, feed_content: str, base_url: str = "") -> DiscoverOutcome:
        parsed = feedparser.parse(feed_content)
        items = []
        for entry in parsed.entries:
            link = getattr(entry, "link", "") or ""
            if not link:
                continue
            items.append(DiscoveredItem(
                url=link,
                title=getattr(entry, "title", "") or "",
                summary=getattr(entry, "summary", "") or "",
                pub_time=_entry_pub_time(entry),
                authors=[a.get("name", "") for a in getattr(entry, "authors", []) if a.get("name")],
            ))
        return DiscoverOutcome(items=items, diagnostics={"entries": len(items)})


class SitemapDiscoverer:
    """以 sitemap.xml 为发现源；支持一层 sitemapindex 递归与 include/exclude 正则过滤。"""

    type = "sitemap"

    def __init__(self, include: str | None = None, exclude: str | None = None, max_urls: int = 500):
        self.include = re.compile(include) if include else None
        self.exclude = re.compile(exclude) if exclude else None
        self.max_urls = max_urls

    def discover(self, xml_content: str, base_url: str = "", fetcher=None) -> DiscoverOutcome:
        urls = self._parse_sitemap(xml_content, fetcher, depth=0)
        filtered = [u for u in urls if self._accept(u)][: self.max_urls]
        return DiscoverOutcome(
            items=[DiscoveredItem(url=u) for u in filtered],
            diagnostics={"urls_total": len(urls), "urls_accepted": len(filtered)},
        )

    def _accept(self, url: str) -> bool:
        if self.include and not self.include.search(url):
            return False
        if self.exclude and self.exclude.search(url):
            return False
        return True

    def _parse_sitemap(self, xml_content: str, fetcher, depth: int) -> list[str]:
        try:
            root = lxml.etree.fromstring(xml_content.encode("utf-8"))
        except (ValueError, lxml.etree.XMLSyntaxError):
            return []
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        tag = lxml.etree.QName(root).localname
        if tag == "sitemapindex" and depth < 1 and fetcher is not None:
            urls: list[str] = []
            for loc in root.findall(".//sm:sitemap/sm:loc", ns):
                if loc.text:
                    try:
                        content, _ = fetcher.fetch(loc.text.strip())
                        urls.extend(self._parse_sitemap(content, fetcher, depth + 1))
                    except FetchError:
                        continue
            return urls
        return [loc.text.strip() for loc in root.findall(".//sm:url/sm:loc", ns) if loc.text]


def _entry_pub_time(entry) -> datetime | None:
    struct = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if struct:
        return datetime(*struct[:6], tzinfo=timezone.utc)
    return None


def build_discoverer(config: dict):
    cfg = (config or {}).get("discoverer") or {"type": "list_page"}
    dtype = cfg.get("type", "list_page")
    if dtype == "rss":
        return RSSDiscoverer()
    if dtype == "sitemap":
        return SitemapDiscoverer(include=cfg.get("include"), exclude=cfg.get("exclude"), max_urls=cfg.get("max_urls", 500))
    return ListPageDiscoverer(min_cluster=cfg.get("min_cluster", MIN_CLUSTER), path_depth=cfg.get("path_depth", PATH_DEPTH))
