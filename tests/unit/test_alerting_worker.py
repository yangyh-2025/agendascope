"""alerting_worker 重试扫描纯逻辑单元测试（T4.15 退避重试执行者）。"""
import os
from types import SimpleNamespace
from unittest.mock import patch

from app.alerting import notifier
from app.worker.alerting_worker import (
    AlertingWorker,
    _parse_retry_at,
    load_smtp_config,
)


def _worker() -> AlertingWorker:
    # session_factory 不触发真实连接（本文件不触碰 db）
    return AlertingWorker(session_factory=lambda: None, smtp_config=None)


class TestParseRetryAt:
    def test_valid_timestamp(self):
        assert _parse_retry_at("1753600000") == 1753600000.0

    def test_empty_and_none(self):
        assert _parse_retry_at("") is None
        assert _parse_retry_at(None) is None

    def test_garbage(self):
        assert _parse_retry_at("not-a-ts") is None


class TestDueRetryChannels:
    def test_due_failed_channel(self):
        alert = SimpleNamespace(notify_result={
            "email": {"status": "failed", "attempts": [{"status": "failed"}], "next_retry_at": "100"},
        })
        due = _worker()._due_retry_channels(alert, now_ts=200)
        assert due == [("email", 1)]

    def test_not_yet_due(self):
        alert = SimpleNamespace(notify_result={
            "email": {"status": "failed", "attempts": [{"status": "failed"}], "next_retry_at": "999"},
        })
        assert _worker()._due_retry_channels(alert, now_ts=200) == []

    def test_ok_channel_skipped(self):
        alert = SimpleNamespace(notify_result={
            "email": {"status": "ok", "attempts": [{"status": "ok"}], "next_retry_at": "100"},
        })
        assert _worker()._due_retry_channels(alert, now_ts=200) == []

    def test_retries_exhausted_skipped(self):
        """首发 + 3 次重试后不再调度重试（转终态处理）。"""
        attempts = [{"status": "failed"}] * (len(notifier.RETRY_BACKOFF_SECONDS) + 1)
        alert = SimpleNamespace(notify_result={
            "webhook": {"status": "failed", "attempts": attempts, "next_retry_at": "100"},
        })
        assert _worker()._due_retry_channels(alert, now_ts=200) == []

    def test_retry_attempt_number_tracks_attempts(self):
        attempts = [{"status": "failed"}] * 3
        alert = SimpleNamespace(notify_result={
            "webhook": {"status": "failed", "attempts": attempts, "next_retry_at": "100"},
        })
        due = _worker()._due_retry_channels(alert, now_ts=200)
        assert due == [("webhook", 3)]

    def test_missing_notify_result(self):
        alert = SimpleNamespace(notify_result=None)
        assert _worker()._due_retry_channels(alert, now_ts=200) == []


class TestLoadSmtpConfig:
    def test_unset_host_returns_none(self):
        with patch.dict(os.environ, {}, clear=True):
            assert load_smtp_config() is None

    def test_host_set(self):
        env = {"SMTP_HOST": "mail.internal", "SMTP_PORT": "2525", "SMTP_TLS": "true"}
        with patch.dict(os.environ, env, clear=True):
            cfg = load_smtp_config()
        assert cfg is not None
        assert cfg.host == "mail.internal" and cfg.port == 2525 and cfg.use_tls
