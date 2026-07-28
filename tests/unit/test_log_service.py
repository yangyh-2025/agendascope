"""日志文件读取单元测试（T5.10）：级别过滤、尾部行数、非 JSON 行兜底。"""
import json

from app.services.log_service import read_log_tail


def _write_log(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            if isinstance(entry, str):
                f.write(entry + "\n")
            else:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class TestReadLogTail:
    def test_level_threshold_filters(self, tmp_path):
        log = tmp_path / "app.log"
        _write_log(log, [
            {"level": "debug", "event": "d1"},
            {"level": "info", "event": "i1"},
            {"level": "warning", "event": "w1"},
            {"level": "error", "event": "e1"},
        ])
        out = read_log_tail(str(log), min_level="WARNING", lines=100)
        assert out["matched"] == 2
        assert any("w1" in line for line in out["items"])
        assert any("e1" in line for line in out["items"])

    def test_lines_limit_returns_tail(self, tmp_path):
        log = tmp_path / "app.log"
        _write_log(log, [{"level": "info", "event": f"ev{i}"} for i in range(10)])
        out = read_log_tail(str(log), min_level="INFO", lines=3)
        assert len(out["items"]) == 3
        assert "ev9" in out["items"][-1]
        assert out["matched"] == 10

    def test_non_json_line_fallback(self, tmp_path):
        log = tmp_path / "app.log"
        _write_log(log, [
            "some plain text without level",
            "2026-07-28 ERROR something broke",
        ])
        out = read_log_tail(str(log), min_level="ERROR", lines=100)
        assert out["matched"] == 1
        assert "something broke" in out["items"][0]
        # 无级别行视为未知级别，保底透传
        out_all = read_log_tail(str(log), min_level="DEBUG", lines=100)
        assert out_all["matched"] == 2

    def test_empty_lines_skipped(self, tmp_path):
        log = tmp_path / "app.log"
        _write_log(log, ["", "   ", {"level": "info", "event": "x"}])
        out = read_log_tail(str(log), min_level="INFO", lines=100)
        assert out["matched"] == 1
