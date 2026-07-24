"""治理状态机纯逻辑单元测试（六态流转/退避/should_crawl/防重③/URL 规范化）。"""
from datetime import datetime, timedelta, timezone

from app.collector.governance import (
    RETRY_BACKOFF_BASE_SECONDS,
    TaskUrlFilter,
    decide_failure,
    should_crawl,
)
from app.collector.utils import normalize_url, url_hash

NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


class TestDecideFailure:
    def test_first_failure_temp_fail_with_backoff(self):
        d = decide_failure(0, 3, NOW)
        assert d.status == "TEMP_FAIL"
        assert d.retry_count == 1
        assert d.next_run_at == NOW + timedelta(seconds=RETRY_BACKOFF_BASE_SECONDS)

    def test_backoff_grows_exponentially(self):
        d1 = decide_failure(0, 3, NOW)
        d2 = decide_failure(1, 3, NOW)
        d3 = decide_failure(2, 3, NOW)
        assert (d1.next_run_at - NOW).total_seconds() == 300
        assert (d2.next_run_at - NOW).total_seconds() == 600
        assert (d3.next_run_at - NOW).total_seconds() == 1200

    def test_third_retry_still_temp_fail(self):
        d = decide_failure(2, 3, NOW)
        assert d.status == "TEMP_FAIL"
        assert d.retry_count == 3

    def test_exceed_max_retries_perm_fail(self):
        d = decide_failure(3, 3, NOW)
        assert d.status == "PERM_FAIL"
        assert d.retry_count == 4
        assert d.next_run_at is None


class TestShouldCrawl:
    def test_pending_always_runnable(self):
        assert should_crawl("PENDING", None, NOW)

    def test_temp_fail_due(self):
        assert should_crawl("TEMP_FAIL", NOW - timedelta(seconds=1), NOW)

    def test_temp_fail_not_due(self):
        assert not should_crawl("TEMP_FAIL", NOW + timedelta(minutes=5), NOW)

    def test_terminal_states_not_runnable(self):
        for state in ("SUCCESS", "PERM_FAIL", "SKIPPED", "RUNNING"):
            assert not should_crawl(state, None, NOW)


class TestTaskUrlFilter:
    def test_in_task_dedup(self):
        f = TaskUrlFilter()
        f.add("abc")
        assert f.seen("abc")
        assert not f.seen("def")
        assert f.filter_new(["abc", "def", "abc"]) == ["def"]


class TestUrlNormalize:
    def test_strips_tracking_params_and_fragment(self):
        assert (
            normalize_url("HTTPS://Example.COM:443/a/?utm_source=x&b=2&a=1#frag")
            == "https://example.com/a?a=1&b=2"
        )

    def test_default_port_removed(self):
        assert normalize_url("http://example.com:80/x") == "http://example.com/x"

    def test_hash_stable_across_param_order(self):
        h1 = url_hash("https://example.com/a?a=1&b=2")
        h2 = url_hash("https://example.com/a/?b=2&a=1&utm_medium=social#c")
        assert h1 == h2
        assert len(h1) == 64
