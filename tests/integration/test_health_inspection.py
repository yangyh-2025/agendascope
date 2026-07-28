"""源健康巡检集成测试（T1.23）：探测走治理状态机 / 成功率告警 / 覆盖率 P0 告警防抖。

网络探测以 monkeypatch 替换 RequestsFetcher  stub——不触外网，但状态机与告警链路走真实 DB/Redis。
"""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import app.collector.health_check as hc
from app.collector.health_check import SourceHealthInspector
from app.collector.types import FetchError
from app.models.alert import Alert
from tests.conftest import make_source

pytestmark = pytest.mark.integration


class _OkFetcher:
    def __init__(self, *args, **kwargs):
        pass

    def fetch(self, url):
        return ("<rss/>", 200)


class _FailFetcher:
    def __init__(self, *args, **kwargs):
        pass

    def fetch(self, url):
        raise FetchError("连接超时")


class TestProbe:
    def test_probe_reachable_recovers_degraded_source(self, db, redis_client, monkeypatch):
        monkeypatch.setattr(hc, "RequestsFetcher", _OkFetcher)
        source = make_source(db, status="degraded", degraded_since=datetime.now(UTC))
        db.commit()
        inspector = SourceHealthInspector(db, redis_client)
        # T1.22 恢复语义：degraded 须连续 2 次成功才回 active（连胜计数在 Redis）
        assert inspector.probe_source(source) is True
        assert source.status == "degraded"
        assert inspector.probe_source(source) is True
        assert source.status == "active"
        assert source.consecutive_failures == 0

    def test_probe_unreachable_drives_state_machine(self, db, redis_client, monkeypatch):
        monkeypatch.setattr(hc, "RequestsFetcher", _FailFetcher)
        source = make_source(db, status="active", consecutive_failures=2)
        db.commit()
        inspector = SourceHealthInspector(db, redis_client)
        assert inspector.probe_source(source) is False
        assert source.consecutive_failures == 3
        assert source.status == "degraded"  # 连续 3 次失败 → degraded（治理状态机裁决）


class TestSuccessRateAlert:
    @staticmethod
    def _make_jobs(db, inspector, source, total, failed):
        now = datetime.now(UTC)
        for i in range(total):
            job = inspector.gov.create_job(source.id, "rss", now - timedelta(minutes=i + 1))
            inspector.gov.mark_running(job)
            if i < failed:
                inspector.gov.mark_failure(job, "模拟抓取失败")
            else:
                inspector.gov.mark_success(job, 5, 1)
        db.commit()

    def test_below_threshold_alerts_with_debounce(self, db, redis_client):
        source = make_source(db)
        db.commit()
        inspector = SourceHealthInspector(db, redis_client)
        # 10 轮 1 失败 → 24h 成功率 90% < 95% 阈值
        self._make_jobs(db, inspector, source, total=10, failed=1)
        assert inspector.check_success_rates([source]) == 1
        alerts = db.scalars(select(Alert)).all()
        assert len(alerts) == 1
        payload = alerts[0].payload
        assert payload["kind"] == "source_success_rate_low"
        assert payload["source_id"] == str(source.id)
        assert payload["threshold"] == 0.95
        # 防抖：同源 1h 内不重复告警
        assert inspector.check_success_rates([source]) == 0

    def test_above_threshold_no_alert(self, db, redis_client):
        source = make_source(db)
        db.commit()
        inspector = SourceHealthInspector(db, redis_client)
        self._make_jobs(db, inspector, source, total=20, failed=0)
        assert inspector.check_success_rates([source]) == 0
        assert db.scalars(select(Alert)).all() == []


class TestCountryCoverageAlert:
    def test_low_coverage_triggers_p0_with_debounce(self, db, redis_client):
        s1 = make_source(db, country_code="US", status="failed")
        s2 = make_source(db, country_code="GB", status="active")
        s3 = make_source(db, country_code="CN", status="degraded")
        db.commit()
        inspector = SourceHealthInspector(db, redis_client)
        # 覆盖率 1/3 ≈ 33% < 70% → P0
        assert inspector.check_country_coverage([s1, s2, s3]) is True
        alerts = db.scalars(select(Alert)).all()
        assert len(alerts) == 1
        payload = alerts[0].payload
        assert payload["kind"] == "country_coverage_low"
        assert payload["severity"] == "P0"
        assert set(payload["uncovered_countries"]) == {"CN", "US"}
        # 防抖 6h：复查不再重复告警
        assert inspector.check_country_coverage([s1, s2, s3]) is False

    def test_full_coverage_no_alert(self, db, redis_client):
        source = make_source(db, country_code="US", status="active")
        db.commit()
        inspector = SourceHealthInspector(db, redis_client)
        assert inspector.check_country_coverage([source]) is False
        assert db.scalars(select(Alert)).all() == []


class TestRunDaily:
    def test_run_daily_full_inspection(self, db, redis_client, monkeypatch):
        monkeypatch.setattr(hc, "RequestsFetcher", _OkFetcher)
        make_source(db, country_code="US", status="active")
        db.commit()
        inspector = SourceHealthInspector(db, redis_client)
        stats = inspector.run_daily()
        assert stats["sources"] == 1
        assert stats["reachable"] == 1
        assert stats["coverage_alert"] is False
