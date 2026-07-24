"""GDELT 采集器单元测试：ArtList 载荷解析、域名→源归属、去重合并（录制式真实结构载荷）。

注：GDELT ArtList JSON 结构（url/title/seendate/domain/language/sourcecountry）为官方文档公开契约；
本测试用符合该契约的样例验证解析与归属逻辑，外部 HTTP 由 stub 替代。
"""
from datetime import datetime, timezone

import pytest

from app.collector.gdelt import GdeltCollector, parse_seen_date
from app.collector.governance import Governance
from app.collector.submitter import Submitter
from tests.conftest import make_source

pytestmark = pytest.mark.integration

# 符合 GDELT DOC 2.0 ArtList 契约的样例载荷
GDELT_ARTLIST = {
    "articles": [
        {
            "url": "https://stub-media.com/world/tariffs-2026",
            "url_mobile": "",
            "title": "US hits dozens of countries with new wave of tariffs",
            "seendate": "20260724T053000Z",
            "socialimage": "",
            "domain": "stub-media.com",
            "language": "English",
            "sourcecountry": "United States",
        },
        {
            "url": "https://unknown-outlet.net/item/42",
            "url_mobile": "",
            "title": "Markets rally as central banks signal coordinated easing",
            "seendate": "20260724T054500Z",
            "socialimage": "",
            "domain": "unknown-outlet.net",
            "language": "English",
            "sourcecountry": "United Kingdom",
        },
    ]
}

ARTICLE_HTML = """
<html><body><article><h1>Headline</h1>
<p>该国政府宣布新的关税措施，涉及数十个贸易伙伴。经济学家警告此举可能推高消费品价格，
并引发连锁报复。多位贸易代表已表态将启动磋商程序，寻求豁免安排。</p>
<p>市场方面，主要股指低开后逐步回稳，分析人士称投资者仍在评估政策的实际影响范围。</p>
</article></body></html>
"""


class CaptureSubmitter(Submitter):
    def __init__(self):
        super().__init__(api_base="http://stub", token="t")
        self.captured: list[dict] = []

    def _post(self, payload: dict) -> bool:
        self.captured.append(payload)
        return True


def test_parse_seen_date():
    dt = parse_seen_date("20260724T053000Z")
    assert dt == datetime(2026, 7, 24, 5, 30, 0, tzinfo=timezone.utc)
    assert parse_seen_date("bad") is None


def test_gdelt_round_resolves_sources_and_dedups(db, redis_client, monkeypatch):
    own = make_source(db, homepage_url="https://stub-media.com", feed_url="https://stub-media.com/feed.xml")
    db.commit()

    gov = Governance(db, redis_client)
    submitter = CaptureSubmitter()
    collector = GdeltCollector(db, gov, submitter)
    monkeypatch.setattr(collector, "fetch_latest", lambda timespan="15min": GDELT_ARTLIST["articles"])
    monkeypatch.setattr(collector.fetcher, "fetch", lambda url: (ARTICLE_HTML, 200))

    found, new = collector.run_round()
    assert found == 2 and new == 2

    by_url = {p["url"]: p for p in submitter.captured}
    # 域名命中已登记源 → 归属该源
    assert by_url["https://stub-media.com/world/tariffs-2026"]["source_id"] == str(own.id)
    # 未命中 → 挂靠 GDELT 兜底伪源
    from app.services.seed_service import GDELT_PSEUDO_SOURCE_NAME
    from app.models.source import Source
    from sqlalchemy import select

    pseudo = db.scalar(select(Source).where(Source.name == GDELT_PSEUDO_SOURCE_NAME))
    assert pseudo is not None and pseudo.collect_mode == "gdelt"
    assert by_url["https://unknown-outlet.net/item/42"]["source_id"] == str(pseudo.id)
    assert by_url["https://stub-media.com/world/tariffs-2026"]["pub_time"] is not None

    # 第二轮：与自有采集按 URL 去重合并（防重①）
    submitter2 = CaptureSubmitter()
    collector2 = GdeltCollector(db, Governance(db, redis_client), submitter2)
    monkeypatch.setattr(collector2, "fetch_latest", lambda timespan="15min": GDELT_ARTLIST["articles"])
    monkeypatch.setattr(collector2.fetcher, "fetch", lambda url: (ARTICLE_HTML, 200))
    _, new2 = collector2.run_round()
    assert new2 == 0
    assert submitter2.captured == []


class TestBufferFallback:
    def test_api_failure_falls_back_to_csv_buffer(self, db, redis_client, monkeypatch, tmp_path):
        """DOC API 故障（429/超时）时降级读本地缓冲 CSV，走同一提交通道。"""
        own = make_source(db, homepage_url="https://stub-media.com", feed_url="https://stub-media.com/feed.xml")
        db.commit()

        gov = Governance(db, redis_client)
        submitter = CaptureSubmitter()
        collector = GdeltCollector(db, gov, submitter)
        monkeypatch.setattr(collector.settings, "gdelt_buffer_dir", str(tmp_path))

        # 先写入一份缓冲（模拟上一轮成功拉取落盘）
        from app.collector.gdelt_buffer import GdeltBuffer

        GdeltBuffer(str(tmp_path)).save_articles(GDELT_ARTLIST["articles"])

        # 本轮 API 故障
        import requests as _requests

        def _boom(*args, **kwargs):
            raise __requests.ConnectionError("429 / timeout")

        monkeypatch.setattr(_requests, "get", _boom)
        monkeypatch.setattr(collector.fetcher, "fetch", lambda url: (ARTICLE_HTML, 200))

        found, new = collector.run_round()
        assert (found, new) == (2, 2)
        by_url = {p["url"]: p for p in submitter.captured}
        assert by_url["https://stub-media.com/world/tariffs-2026"]["source_id"] == str(own.id)
        assert by_url["https://unknown-outlet.net/item/42"]["pub_time"] is not None

    def test_api_success_refreshes_buffer(self, db, redis_client, monkeypatch, tmp_path):
        """API 成功时自动刷新缓冲 CSV。"""
        make_source(db, homepage_url="https://stub-media.com", feed_url="https://stub-media.com/feed.xml")
        db.commit()
        collector = GdeltCollector(db, Governance(db, redis_client), CaptureSubmitter())
        monkeypatch.setattr(collector.settings, "gdelt_buffer_dir", str(tmp_path))
        monkeypatch.setattr(collector, "fetch_latest", lambda timespan="15min": GDELT_ARTLIST["articles"])
        monkeypatch.setattr(collector.fetcher, "fetch", lambda url: (ARTICLE_HTML, 200))

        collector.run_round()
        from app.collector.gdelt_buffer import GdeltBuffer

        rows = GdeltBuffer(str(tmp_path)).read_latest()
        assert len(rows) == 2
        assert rows[0]["url"] == GDELT_ARTLIST["articles"][0]["url"]
