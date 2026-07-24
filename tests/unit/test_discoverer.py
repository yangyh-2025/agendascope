"""ListPageDiscoverer 链接签名聚类单元测试（详细设计算法 6）。"""
from app.collector.discoverer import (
    ListPageDiscoverer,
    RSSDiscoverer,
    SitemapDiscoverer,
    path_signature,
)
import lxml.html

LIST_HTML = """
<html><body>
<nav class="menu"><a href="/about">关于</a><a href="/contact">联系</a><a href="/tag/x">标签</a></nav>
<div class="content"><ul class="news-list">
<li class="item-101 odd"><a href="/news/1001">标题一</a></li>
<li class="item-102 even"><a href="/news/1002">标题二</a></li>
<li class="item-103 active"><a href="/news/1003#top">标题三</a></li>
<li class="item-104"><a href="/news/1004">标题四</a></li>
<li class="item-105"><a href="/news/1005">标题五</a></li>
<li class="item-106"><a href="https://example.com/news/1006">标题六</a></li>
</ul></div>
<footer><a href="https://external.com/x">外链</a><a href="javascript:void(0)">js</a></footer>
</body></html>
"""


class TestPathSignature:
    def test_noise_classes_removed_and_digits_generalized(self):
        doc = lxml.html.fromstring(LIST_HTML)
        anchor = next(a for a in doc.iter("a") if a.get("href", "").startswith("/news/1001"))
        sig = path_signature(anchor)
        assert "item-N" in sig
        assert "odd" not in sig
        assert "101" not in sig


class TestListPageDiscoverer:
    def test_max_cluster_selected(self):
        outcome = ListPageDiscoverer().discover(LIST_HTML, "https://example.com/latest")
        diag = outcome.diagnostics
        assert diag["article_cluster_size"] == 6
        assert diag["signature"] is not None and "item-N" in diag["signature"]
        urls = [i.url for i in outcome.items]
        assert "https://example.com/news/1003" in urls  # 锚点已剔除
        assert all("example.com" in u for u in urls)    # 同站过滤
        assert len(set(urls)) == len(urls)              # 去重

    def test_below_min_cluster_returns_empty(self):
        html = "<div><p><a href='/a'>1</a><a href='/b'>2</a><a href='/c'>3</a></p></div>"
        outcome = ListPageDiscoverer(min_cluster=5).discover(html, "https://example.com/")
        assert outcome.items == []
        assert outcome.diagnostics["signature"] is None

    def test_min_cluster_boundary(self):
        html = "<ul>" + "".join(f"<li class='x'><a href='/n/{i}'>t</a></li>" for i in range(5)) + "</ul>"
        outcome = ListPageDiscoverer(min_cluster=5).discover(html, "https://example.com/")
        assert len(outcome.items) == 5


FEED_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>T</title>
<item><title>A</title><link>https://x.com/1</link><pubDate>Thu, 24 Jul 2026 10:00:00 GMT</pubDate></item>
<item><title>B</title><link>https://x.com/2</link></item>
</channel></rss>"""

SITEMAP_XML = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://x.com/news/a</loc></url>
<url><loc>https://x.com/about</loc></url>
</urlset>"""


class TestRSSDiscoverer:
    def test_parse_entries(self):
        outcome = RSSDiscoverer().discover(FEED_XML)
        assert len(outcome.items) == 2
        assert outcome.items[0].title == "A"
        assert outcome.items[0].pub_time is not None


class TestSitemapDiscoverer:
    def test_parse_with_include_filter(self):
        d = SitemapDiscoverer(include=r"/news/")
        outcome = d.discover(SITEMAP_XML)
        assert [i.url for i in outcome.items] == ["https://x.com/news/a"]
