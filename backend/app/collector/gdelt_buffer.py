"""GDELT 15 分钟批 CSV 本地缓冲（T1.19）：API 故障时降级读缓冲。

每轮成功拉取 GDELT DOC 2.0 ArtList 后，将结果以 GDELT CSV 契约（url,url_mobile,title,
seendate,socialimage,domain,language,sourcecountry）落盘本地缓冲目录；当 DOC API 故障
（429/超时/5xx/连接失败）时，降级解析最近一份缓冲 CSV，走同一 /internal/collect 通道。
缓冲按时间戳滚动，默认保留 96 份（24h）。
"""
import csv
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger("gdelt.buffer")

CSV_FIELDS = ["url", "url_mobile", "title", "seendate", "socialimage", "domain", "language", "sourcecountry"]
DEFAULT_KEEP = 96


@dataclass
class GdeltBuffer:
    buffer_dir: str
    keep: int = DEFAULT_KEEP

    def __post_init__(self):
        Path(self.buffer_dir).mkdir(parents=True, exist_ok=True)

    def _files(self) -> list[Path]:
        return sorted(Path(self.buffer_dir).glob("gdelt_artlist_*.csv"))

    def save_articles(self, articles: list[dict], now: datetime | None = None) -> Path:
        """将本轮 ArtList 文章写成缓冲 CSV，并按 keep 滚动清理旧文件。"""
        now = now or datetime.now(timezone.utc)
        path = Path(self.buffer_dir) / f"gdelt_artlist_{now.strftime('%Y%m%dT%H%M%SZ')}.csv"
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for item in articles:
            writer.writerow({k: item.get(k, "") for k in CSV_FIELDS})
        path.write_text(buf.getvalue(), encoding="utf-8")
        # 滚动清理
        files = self._files()
        for old in files[: max(len(files) - self.keep, 0)]:
            old.unlink(missing_ok=True)
        logger.info("gdelt_buffer_saved", path=str(path), rows=len(articles))
        return path

    def latest(self) -> Path | None:
        files = self._files()
        return files[-1] if files else None

    def read_latest(self) -> list[dict]:
        """读取最近一份缓冲 CSV，返回与 ArtList 同构的文章字典列表；无缓冲返回空列表。"""
        path = self.latest()
        if path is None:
            logger.warning("gdelt_buffer_empty")
            return []
        with open(path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        logger.warning("gdelt_buffer_fallback", path=str(path), rows=len(rows), reason="doc_api_unavailable")
        return rows
