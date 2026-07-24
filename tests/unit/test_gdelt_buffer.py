"""GDELT 缓冲读写单元测试（不依赖 DB/网络）。"""
from datetime import datetime, timedelta, timezone

from app.collector.gdelt_buffer import CSV_FIELDS, GdeltBuffer

ARTICLES = [
    {"url": "https://a.com/1", "title": "t1", "seendate": "20260724T053000Z",
     "domain": "a.com", "language": "English", "sourcecountry": "United States"},
    {"url": "https://b.com/2", "title": "t2", "seendate": "20260724T054500Z",
     "domain": "b.com", "language": "English", "sourcecountry": "United Kingdom"},
]


def test_save_and_read_latest(tmp_path):
    buf = GdeltBuffer(str(tmp_path))
    buf.save_articles(ARTICLES, now=datetime(2026, 7, 24, 6, 0, tzinfo=timezone.utc))
    rows = buf.read_latest()
    assert len(rows) == 2
    assert rows[0]["url"] == "https://a.com/1"
    assert rows[1]["domain"] == "b.com"
    assert set(rows[0].keys()) == set(CSV_FIELDS)


def test_rolling_keep(tmp_path):
    buf = GdeltBuffer(str(tmp_path), keep=3)
    base = datetime(2026, 7, 24, 0, 0, tzinfo=timezone.utc)
    for i in range(5):
        buf.save_articles(ARTICLES, now=base + timedelta(minutes=15 * i))
    assert len(list(tmp_path.glob("gdelt_artlist_*.csv"))) == 3
    latest = buf.latest()
    assert "010000" in latest.name  # 04:15 批次保留


def test_empty_buffer_returns_empty(tmp_path):
    buf = GdeltBuffer(str(tmp_path))
    assert buf.read_latest() == []
