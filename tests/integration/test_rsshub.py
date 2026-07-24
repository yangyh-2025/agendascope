"""RSSHub 补源通道：feed 地址解析与 rsshub 源采集（T1.20）。"""
import pytest

from app.collector.fetcher import RequestsFetcher
from app.collector.governance import Governance
from app.collector.rss_collector import RssCollector, resolve_feed_url
from app.collector.submitter import Submitter
from app.core.errors import BizError
from app.schemas.source import SourceCreate
from app.services.source_service import SourceService
from tests.conftest import make_source
from tests.integration.test_collector_flow import ARTICLE_HTML, FEED_XML, CaptureSubmitter, StubFetcher

pytestmark = pytest.mark.integration


class _Settings:
    rsshub_base = "http://rsshub:1200"


class TestResolveFeedUrl:
    def test_rss_mode_uses_feed_url(self, db):
        source = make_source(db, feed_url="https://x.com/feed.xml", collect_mode="rss")
        assert resolve_feed_url(source) == "https://x.com/feed.xml"

    def test_rsshub_mode_builds_from_route(self, db):
        source = make_source(
            db, feed_url=None, collect_mode="rsshub",
            crawl_config={"rsshub_route": "/bbc/news/world"},
        )
        assert resolve_feed_url(source, _Settings()) == "http://rsshub:1200/bbc/news/world"

    def test_rsshub_mode_missing_route_returns_none(self, db):
        source = make_source(db, feed_url=None, collect_mode="rsshub", crawl_config={})
        assert resolve_feed_url(source, _Settings()) is None


class TestRsshubSourceRound:
    def test_rsshub_source_collects_via_rss_path(self, db, redis_client):
        source = make_source(
            db, feed_url=None, collect_mode="rsshub",
            crawl_config={"rsshub_route": "/stub/feed.xml"},
        )
        db.commit()
        gov = Governance(db, redis_client)
        from datetime import datetime, timezone

        job = gov.create_job(source.id, "rsshub", datetime.now(timezone.utc))
        submitter = CaptureSubmitter()

        # StubFetcher 对任意 feed.xml 返回 FEED_XML；RSSHub 路由地址以 /stub/feed.xml 结尾
        collector = RssCollector(gov, submitter, fetcher=StubFetcher())
        found, new = collector.run_round(source, job)
        assert found == 2 and new == 2
        assert all(p["content_status"] == "full" for p in submitter.captured)


class TestRsshubSourceValidation:
    def test_rsshub_mode_requires_route(self, db):
        service = SourceService(db)
        body = SourceCreate(
            name="NoRoute", country_code="US", homepage_url="https://x.com",
            collect_mode="rsshub", media_type="online", language="en",
        )
        with pytest.raises(BizError) as exc:
            service.create(body)
        assert exc.value.code == 1001
