"""alerting.subscription 纯逻辑单元测试（T4.16）：到期判定 / 摘要渲染 / 退订文案。"""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.alerting.subscription import is_due, render_digest_text

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


def _sub(**overrides):
    defaults = {
        "frequency": "daily",
        "last_sent_at": None,
        "country_codes": ["US", "JP"],
        "topic_category": None,
        "locale": "zh-CN",
        "unsubscribe_token": "tok-abc",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestIsDue:
    def test_never_sent_is_due(self):
        assert is_due(_sub(), NOW)

    def test_daily_sent_today_not_due(self):
        assert not is_due(_sub(last_sent_at=NOW - timedelta(hours=2)), NOW)

    def test_daily_sent_yesterday_due(self):
        assert is_due(_sub(last_sent_at=NOW - timedelta(days=1)), NOW)

    def test_weekly_sent_3d_ago_not_due(self):
        sub = _sub(frequency="weekly", last_sent_at=NOW - timedelta(days=3))
        assert not is_due(sub, NOW)

    def test_weekly_sent_7d_ago_due(self):
        sub = _sub(frequency="weekly", last_sent_at=NOW - timedelta(days=7, hours=1))
        assert is_due(sub, NOW)

    def test_naive_last_sent_handled(self):
        naive = datetime(2026, 7, 27, 12, 0)
        assert is_due(_sub(last_sent_at=naive), NOW)


class TestRenderDigestText:
    def _digest(self, items):
        return {
            "frequency": "daily",
            "period_days": 1,
            "window_start": "2026-07-27T12:00:00+00:00",
            "window_end": "2026-07-28T12:00:00+00:00",
            "country_codes": ["US", "JP"],
            "topic_category": None,
            "items": items,
        }

    def test_render_with_items(self):
        digest = self._digest([{
            "country_code": "US", "topic_name": "新疆棉争议", "summary": "摘要文本",
            "article_count": 42, "salience_rank": 1, "sentiment_neg": 0.3,
        }])
        title, body = render_digest_text(_sub(), digest, "http://x/api/v1/subscriptions/unsubscribe?token=tok-abc")
        assert "日报" in title and "US,JP" in title
        assert "新疆棉争议" in body and "42 篇" in body
        assert "退订" in body and "token=tok-abc" in body

    def test_render_empty_digest(self):
        title, body = render_digest_text(_sub(), self._digest([]), "http://x/unsub")
        assert "无达到显著性阈值的议题" in body

    def test_weekly_label(self):
        title, _ = render_digest_text(_sub(frequency="weekly"), self._digest([]), "http://x/unsub")
        assert "周报" in title
