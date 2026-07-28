"""磁盘清理单元测试（T5.10）：过期文件识别与删除；阈值判定（DB 告警见集成测试）。"""
import os
from datetime import UTC, datetime

from app.services import maintenance_service
from app.services.maintenance_service import (
    cleanup_expired_files,
    disk_usage_percent,
    find_expired_files,
    run_disk_cleanup,
)

NOW = datetime(2026, 7, 28, tzinfo=UTC)


def _make_file(path, age_days):
    path.write_text("x" * 128, encoding="utf-8")
    mtime = NOW.timestamp() - age_days * 86400
    os.utime(path, (mtime, mtime))


class TestFindExpiredFiles:
    def test_old_file_matched_recent_kept(self, tmp_path):
        old = tmp_path / "old.html"
        recent = tmp_path / "recent.html"
        _make_file(old, age_days=120)
        _make_file(recent, age_days=10)
        found = find_expired_files([str(tmp_path)], retention_days=90, now=NOW)
        assert found == [old]

    def test_missing_directory_ignored(self, tmp_path):
        assert find_expired_files([str(tmp_path / "nope")], 90, NOW) == []

    def test_subdirectory_not_recursed(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        _make_file(sub / "old.html", age_days=120)
        assert find_expired_files([str(tmp_path)], 90, NOW) == []


class TestCleanupExpiredFiles:
    def test_delete_stats(self, tmp_path):
        _make_file(tmp_path / "a.html", age_days=100)
        _make_file(tmp_path / "b.html", age_days=200)
        _make_file(tmp_path / "c.html", age_days=5)
        stats = cleanup_expired_files([str(tmp_path)], 90, NOW)
        assert stats["deleted"] == 2
        assert stats["freed_bytes"] == 256
        assert stats["errors"] == []
        assert (tmp_path / "c.html").exists()


class TestRunDiskCleanup:
    def test_below_threshold_no_action(self, monkeypatch):
        monkeypatch.setattr(maintenance_service, "disk_usage_percent", lambda path="/": 50.0)
        # 未超阈值时不触库，db 传 None 即可
        result = run_disk_cleanup(None)
        assert result["triggered"] is False
        assert result["disk_percent"] == 50.0
        assert result["deleted"] == 0

    def test_disk_usage_percent_real(self):
        percent = disk_usage_percent("/")
        assert 0 < percent < 100
