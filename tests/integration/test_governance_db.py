"""治理状态机六态流转 + 源健康状态机 + 失败率告警（DB 集成）。"""
from datetime import UTC, datetime, timedelta

import pytest

from app.collector.governance import Governance
from app.models.alert import Alert
from tests.conftest import make_source

pytestmark = pytest.mark.integration

NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC)


class TestJobStateMachine:
    def test_full_success_flow(self, db, redis_client):
        source = make_source(db)
        db.commit()
        gov = Governance(db, redis_client)
        job = gov.create_job(source.id, "rss", NOW)
        assert job.status == "PENDING"
        assert gov.job_should_run(job, NOW)

        gov.mark_running(job)
        assert job.status == "RUNNING"
        assert not gov.job_should_run(job, NOW)  # RUNNING 不可重复调度

        gov.mark_success(job, articles_found=20, articles_new=5)
        assert job.status == "SUCCESS"
        assert job.articles_found == 20 and job.articles_new == 5

    def test_temp_fail_to_perm_fail_after_3_retries(self, db, redis_client):
        source = make_source(db)
        db.commit()
        gov = Governance(db, redis_client)
        job = gov.create_job(source.id, "rss", NOW)

        for expected in (("TEMP_FAIL", 1), ("TEMP_FAIL", 2), ("TEMP_FAIL", 3), ("PERM_FAIL", 4)):
            gov.mark_running(job)
            decision = gov.mark_failure(job, "模拟抓取失败")
            assert (decision.status, decision.retry_count) == expected
        assert job.status == "PERM_FAIL"
        assert job.next_run_at is None
        assert not gov.job_should_run(job, NOW + timedelta(days=1))  # 终态不再调度

    def test_temp_fail_backoff_due(self, db, redis_client):
        source = make_source(db)
        db.commit()
        gov = Governance(db, redis_client)
        now = datetime.now(UTC)
        job = gov.create_job(source.id, "rss", now)
        gov.mark_running(job)
        gov.mark_failure(job, "超时")
        assert job.status == "TEMP_FAIL"
        assert not gov.job_should_run(job, now + timedelta(seconds=60))   # 退避窗口内
        assert gov.job_should_run(job, now + timedelta(seconds=301))      # 到期可重跑

    def test_skipped_state(self, db, redis_client):
        source = make_source(db)
        db.commit()
        gov = Governance(db, redis_client)
        job = gov.create_job(source.id, "rss", NOW)
        gov.mark_skipped(job, "should_crawl 裁决去重跳过")
        assert job.status == "SKIPPED"
        assert not gov.job_should_run(job, NOW)


class TestDedupLayer1:
    def test_persistent_dedup_via_fingerprint_and_db(self, db, redis_client):
        gov = Governance(db, redis_client)
        assert not gov.is_duplicate("a" * 64)
        gov.record_fingerprint("a" * 64)
        assert gov.is_duplicate("a" * 64)


class TestSourceHealth:
    def test_active_to_degraded_after_3_failures(self, db, redis_client):
        source = make_source(db)
        db.commit()
        gov = Governance(db, redis_client)
        assert gov.update_source_health(source, False) is None
        assert gov.update_source_health(source, False) is None
        assert gov.update_source_health(source, False) == "degraded"
        assert source.status_history[-1]["to"] == "degraded"

    def test_degraded_to_failed_after_24h(self, db, redis_client):
        source = make_source(db)
        db.commit()
        gov = Governance(db, redis_client)
        for _ in range(3):
            gov.update_source_health(source, False)
        source.degraded_since = datetime.now(UTC) - timedelta(hours=25)
        assert gov.update_source_health(source, False) == "failed"

    def test_recovery_requires_two_consecutive_successes(self, db, redis_client):
        source = make_source(db)
        db.commit()
        gov = Governance(db, redis_client)
        for _ in range(3):
            gov.update_source_health(source, False)
        assert source.status == "degraded"
        # 第 1 次成功：仍为 degraded
        assert gov.update_source_health(source, True) is None
        assert source.status == "degraded"
        # 第 2 次连续成功：恢复 active
        assert gov.update_source_health(source, True) == "active"
        assert source.consecutive_failures == 0
        assert source.last_success_at is not None

    def test_recovery_streak_reset_on_failure(self, db, redis_client):
        source = make_source(db)
        db.commit()
        gov = Governance(db, redis_client)
        for _ in range(3):
            gov.update_source_health(source, False)
        gov.update_source_health(source, True)      # 连胜 1
        gov.update_source_health(source, False)     # 失败清零连胜（degraded 未超 24h 不变 failed）
        assert source.status == "degraded"
        assert gov.update_source_health(source, True) is None   # 重新计 1
        assert source.status == "degraded"
        assert gov.update_source_health(source, True) == "active"


class TestFailRateAlert:
    def test_alert_written_when_fail_rate_exceeds_threshold(self, db, redis_client):
        source = make_source(db)
        db.commit()
        gov = Governance(db, redis_client)
        # 24h 窗口内 1 成功 9 失败 → 失败率 90% > 10% 阈值
        now = datetime.now(UTC)
        for i in range(10):
            job = gov.create_job(source.id, "rss", now + timedelta(minutes=i))
            gov.mark_running(job)
            if i == 0:
                gov.mark_success(job, 10, 2)
            else:
                gov.mark_failure(job, "失败")
        db.commit()

        assert gov.source_fail_rate(source.id) == pytest.approx(0.9)
        assert gov.maybe_alert_source_fail_rate(source) is True
        alerts = db.query(Alert).all()
        assert len(alerts) == 1
        assert alerts[0].payload["kind"] == "source_fail_rate"
        assert alerts[0].payload["source_id"] == str(source.id)

        # 防抖：1h 内不重复告警
        assert gov.maybe_alert_source_fail_rate(source) is False
        assert db.query(Alert).count() == 1

    def test_no_alert_below_threshold(self, db, redis_client):
        source = make_source(db)
        db.commit()
        gov = Governance(db, redis_client)
        now = datetime.now(UTC)
        for i in range(10):
            job = gov.create_job(source.id, "rss", now + timedelta(minutes=i))
            gov.mark_running(job)
            if i < 9:
                gov.mark_success(job, 10, 2)
            else:
                gov.mark_failure(job, "失败")
        db.commit()
        assert gov.maybe_alert_source_fail_rate(source) is False
        assert db.query(Alert).count() == 0
