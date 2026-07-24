"""RSS 采集器与配置驱动管线（pipeline）集成测试：stub 网络层，验证发现→去重→抽取→提交全链路。"""
from datetime import UTC

import pytest

from app.collector.fetcher import RequestsFetcher
from app.collector.governance import Governance
from app.collector.pipeline import PipelineCollector
from app.collector.rss_collector import RssCollector
from app.collector.submitter import Submitter
from app.collector.types import FetchError
from tests.conftest import make_source

pytestmark = pytest.mark.integration

FEED_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Stub Feed</title>
<item><title>央行宣布降息二十五个基点</title><link>https://stub-media.com/news/1</link>
<pubDate>Thu, 24 Jul 2026 10:00:00 GMT</pubDate><summary>降息摘要</summary></item>
<item><title>议会通过新能源法案</title><link>https://stub-media.com/news/2</link>
<pubDate>Thu, 24 Jul 2026 11:00:00 GMT</pubDate><summary>能源法案摘要</summary></item>
</channel></rss>"""

ARTICLE_HTML = """
<html><body><article>
<h1>央行宣布降息二十五个基点</h1>
<p>该国央行今日宣布降息二十五个基点，以应对经济增长放缓的压力。行长在发布会上表示，
未来货币政策将保持适度宽松，重点关注就业与物价稳定两大目标。</p>
<p>分析师普遍认为，本轮降息周期仍有余地，预计年内可能再降一次。消息公布后，
该国主要股指小幅收涨，汇率保持平稳，债券市场收益率下行。</p>
</article></body></html>
"""

LIST_PAGE_HTML = """
<html><body><div class="content"><ul>
<li class="item-1 odd"><a href="/news/1">央行宣布降息二十五个基点</a></li>
<li class="item-2 even"><a href="/news/2">议会通过新能源法案</a></li>
<li class="item-3"><a href="/news/3">第三条新闻标题内容</a></li>
<li class="item-4"><a href="/news/4">第四条新闻标题内容</a></li>
<li class="item-5"><a href="/news/5">第五条新闻标题内容</a></li>
</ul></div></body></html>
"""


class StubFetcher(RequestsFetcher):
    def __init__(self):
        pass  # 不建真实会话

    def fetch(self, url: str):
        if url.endswith("feed.xml"):
            return FEED_XML, 200
        if url.endswith("/latest"):
            return LIST_PAGE_HTML, 200
        return ARTICLE_HTML, 200


class CaptureSubmitter(Submitter):
    def __init__(self):
        super().__init__(api_base="http://stub", token="t")
        self.captured: list[dict] = []

    def _post(self, payload: dict) -> bool:
        self.captured.append(payload)
        return True


class TestRssCollector:
    def test_round_collects_and_dedups(self, db, redis_client):
        source = make_source(db, feed_url="https://stub-media.com/feed.xml")
        db.commit()
        gov = Governance(db, redis_client)
        job = gov.create_job(source.id, "rss", __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
        submitter = CaptureSubmitter()
        collector = RssCollector(gov, submitter, fetcher=StubFetcher())

        found, new = collector.run_round(source, job)
        assert found == 2 and new == 2
        assert {p["url"] for p in submitter.captured} == {
            "https://stub-media.com/news/1", "https://stub-media.com/news/2",
        }
        payload = submitter.captured[0]
        assert payload["content_status"] == "full"
        assert len(payload["content"]) >= 10
        assert payload["pub_time"] is not None

        # 第二轮：防重①持久去重生效，新入库为 0
        submitter2 = CaptureSubmitter()
        collector2 = RssCollector(Governance(db, redis_client), submitter2, fetcher=StubFetcher())
        # 模拟已入库：第一轮指纹已写 redis + 模拟 articles 行
        from app.collector.utils import url_hash
        from app.models.article import Article

        db.add(Article(
            source_id=source.id, url="https://stub-media.com/news/1",
            url_hash=url_hash("https://stub-media.com/news/1"), title="t", content="c",
            language="en", published_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            country_code="US",
        ))
        db.commit()
        found2, new2 = collector2.run_round(source, job)
        assert new2 == 0


class TestPipelineCollector:
    def test_list_page_pipeline_round(self, db, redis_client):
        source = make_source(
            db,
            feed_url=None,
            adapter_type="pipeline",
            crawl_config={
                "fetcher": {"type": "requests"},
                "discoverer": {"type": "list_page"},
                "extractor": {"type": "trafilatura"},
                "entry_points": ["https://stub-media.com/latest"],
            },
        )
        db.commit()
        gov = Governance(db, redis_client)
        job = gov.create_job(source.id, "rss", __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
        submitter = CaptureSubmitter()
        collector = PipelineCollector(gov, submitter)

        import app.collector.fetcher as fetcher_module
        import app.collector.pipeline as pipeline_module

        original = fetcher_module.build_fetcher
        pipeline_module.build_fetcher = lambda config, country_code="": StubFetcher()
        try:
            found, new = collector.run_round(source, job)
        finally:
            pipeline_module.build_fetcher = original

        assert found == 5 and new == 5
        assert all(p["adapter_type"] == "pipeline" for p in submitter.captured)
        assert all(p["content_status"] == "full" for p in submitter.captured)


FEED_WITH_ENCODED = """<?xml version="1.0"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>Encoded Feed</title>
<item><title>央行宣布降息二十五个基点</title><link>https://stub-media.com/news/9</link>
<pubDate>Thu, 24 Jul 2026 10:00:00 GMT</pubDate>
<content:encoded><![CDATA[<article><p>该国央行今日宣布降息二十五个基点，以应对经济增长放缓的压力。
行长表示未来货币政策将保持适度宽松，重点关注就业与物价稳定。分析师预计年内仍有一次降息空间，
消息公布后主要股指收涨，汇率保持平稳，市场流动性合理充裕，债券市场收益率小幅下行。</p></article>]]></content:encoded>
</item>
</channel></rss>"""


class FailOnArticleFetcher(StubFetcher):
    """feed 可达、正文页全部 403（模拟反爬站点）。"""

    def fetch(self, url: str):
        if url.endswith("feed.xml"):
            return FEED_WITH_ENCODED, 200
        raise FetchError("HTTP 403", http_status=403)


class TestFeedEncodedPreference:
    def test_encoded_fulltext_used_when_article_blocked(self, db, redis_client):
        """content:encoded 自带全文时，即使正文页 403 也能产出 full。"""
        source = make_source(db, feed_url="https://stub-media.com/feed.xml")
        db.commit()
        gov = Governance(db, redis_client)
        from datetime import datetime

        job = gov.create_job(source.id, "rss", datetime.now(UTC))
        submitter = CaptureSubmitter()
        collector = RssCollector(gov, submitter, fetcher=FailOnArticleFetcher())
        found, new = collector.run_round(source, job)
        assert (found, new) == (1, 1)
        payload = submitter.captured[0]
        assert payload["content_status"] == "full"
        assert "降息二十五个基点" in payload["content"]
