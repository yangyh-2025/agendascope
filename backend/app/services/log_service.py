"""应用日志文件读取（T5.10）：管理后台日志查看与诊断包共用。

日志为 structlog JSON Lines；按级别阈值过滤（>= 请求级别），从文件尾部读取，
避免全量加载大文件。非 JSON 行（第三方库裸输出）保底按子串匹配级别词。
"""
import json
import os

_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
# 单次最多从文件尾部回读 4MB，防止巨型日志拖垮请求
_TAIL_READ_BYTES = 4 * 1024 * 1024


def _line_level(line: str) -> str | None:
    """提取单行日志级别（大写）；无法识别返回 None。"""
    try:
        parsed = json.loads(line)
    except ValueError:
        upper = line.upper()
        for level in ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"):
            if level in upper:
                return level
        return None
    if isinstance(parsed, dict):
        raw_level = parsed.get("level")
        if isinstance(raw_level, str):
            return raw_level.upper()
    return None


def read_log_tail(path: str, min_level: str = "DEBUG", lines: int = 200) -> dict:
    """读取日志文件尾部：返回 {items, matched, truncated}；文件不存在抛 FileNotFoundError。"""
    threshold = _LEVEL_ORDER.get(min_level.upper(), 10)
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if size > _TAIL_READ_BYTES:
            f.seek(-_TAIL_READ_BYTES, os.SEEK_END)
        raw = f.read()
    text = raw.decode("utf-8", errors="replace")
    rows = text.splitlines()
    if size > _TAIL_READ_BYTES and rows:
        rows = rows[1:]  # 首行可能被截断，丢弃半行
    matched: list[str] = []
    for line in rows:
        line = line.strip()
        if not line:
            continue
        level = _line_level(line)
        if level is None:
            # 无法识别级别的行（裸输出/堆栈续行）仅在最宽阈值下透传，避免污染高级别视图
            if threshold <= _LEVEL_ORDER["DEBUG"]:
                matched.append(line)
        elif _LEVEL_ORDER.get(level, 0) >= threshold:
            matched.append(line)
    return {
        "items": matched[-lines:],
        "matched": len(matched),
        "truncated": size > _TAIL_READ_BYTES,
    }
