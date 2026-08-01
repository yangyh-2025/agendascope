"""源健康巡检（T1.23）：每日全量 + 每小时重点源。

- 真实探测源可达性（feed/homepage 实际抓取）并走采集治理状态机（T1.22 同一套裁决）
- 源 24h 采集成功率（collection_jobs 滑动窗口）跌破阈值 → 主动告警（防抖 1h）
- 国家覆盖率（有 ≥1 个 active 源的国家占比）跌破阈值 → P0 主动告警（防抖 6h）

告警风格沿用 governance.maybe_alert_source_fail_rate：防抖键 + 系统内置规则 + alerts 表。
离线模式（offline_mode）下调度器不下发巡检——探测本身即外联，与外部拉取一并禁止。
"""
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector.fetcher import RequestsFetcher
from app.collector.governance import Governance, write_system_alert
from app.collector.rss_collector import resolve_feed_url
from app.collector.types import FetchError
from app.config import get_settings
from app.core.logging import get_logger
from app.models.source import Source

logger = get_logger("health_check")

# 重点源判定：poll_interval_min ≤ 该值，或 crawl_config.focus_source = true
FOCUS_POLL_INTERVAL_MAX_MIN = 5

_COVERAGE_DEBOUNCE_KEY = "alert:country_coverage_low"
_COVERAGE_DEBOUNCE_SECONDS = 6 * 3600
_SUCCESS_RATE_DEBOUNCE_SECONDS = 3600


def is_focus_source(poll_interval_min: int | None, crawl_config: dict | None) -> bool:
    """重点源裁决（纯函数）：高频源（≤5min）或配置显式标记。"""
    if (poll_interval_min or FOCUS_POLL_INTERVAL_MAX_MIN) <= FOCUS_POLL_INTERVAL_MAX_MIN:
        return True
    return bool((crawl_config or {}).get("focus_source"))


def compute_country_coverage(sources: list[Source]) -> tuple[float, list[str], list[str]]:
    """国家覆盖率（纯函数）：返回 (覆盖率, 已覆盖国家, 未覆盖国家)。

    分母为已登记源的国家集合（不含 GDELT 伪源 ZZ）；分子为至少有 1 个 active 源的国家。
    """
    countries = sorted({s.country_code for s in sources if s.country_code != "ZZ"})
    covered = sorted({s.country_code for s in sources if s.country_code != "ZZ" and s.status == "active"})
    uncovered = [c for c in countries if c not in covered]
    if not countries:
        return 1.0, covered, uncovered
    return len(covered) / len(countries), covered, uncovered


class SourceHealthInspector:
    """源健康巡检器：探测可达性 + 成功率/覆盖率主动告警。"""

    def __init__(self, db: Session, redis_client=None):
        self.db = db
        self.redis = redis_client
        self.settings = get_settings()
        self.gov = Governance(db, redis_client)

    # ---------- 探测 ----------

    def _eligible_sources(self) -> list[Source]:
        """巡检对象：自有采集源（GDELT 伪源为外联兜底通道，不按源巡检）。"""
        return list(self.db.scalars(select(Source).where(Source.collect_mode != "gdelt")).all())

    def probe_source(self, source: Source) -> bool:
        """真实探测源可达性并推进治理状态机；返回是否可达。"""
        url = resolve_feed_url(source) or source.homepage_url
        insecure_ssl = bool((source.crawl_config or {}).get("insecure_ssl"))
        try:
            RequestsFetcher(
                country_code=source.country_code,
                verify=not insecure_ssl,
            ).fetch(url)
        except FetchError as exc:
            self.gov.update_source_health(source, False, reason=f"巡检探测失败: {exc}"[:200])
            return False
        except Exception as exc:  # noqa: BLE001 网络层异常同样记为不可达，绝不静默
            self.gov.update_source_health(source, False, reason=f"巡检探测异常: {exc}"[:200])
            return False
        self.gov.update_source_health(source, True, reason="巡检探测可达")
        return True

    # ---------- 巡检任务 ----------

    def run_daily(self) -> dict:
        """每日全量巡检：全部自有源探测 + 成功率告警检查 + 国家覆盖率 P0 检查。"""
        sources = self._eligible_sources()
        reachable = sum(1 for s in sources if self.probe_source(s))
        success_alerts = self.check_success_rates(sources)
        coverage_alert = self.check_country_coverage(sources)
        self.db.flush()
        stats = {
            "sources": len(sources),
            "reachable": reachable,
            "success_rate_alerts": success_alerts,
            "coverage_alert": coverage_alert,
        }
        logger.info("daily_health_inspection", **stats)
        return stats

    def run_hourly(self) -> dict:
        """每小时重点源巡检：poll_interval_min ≤5 或 crawl_config.focus_source 标记的源。"""
        focus = [s for s in self._eligible_sources() if is_focus_source(s.poll_interval_min, s.crawl_config)]
        reachable = sum(1 for s in focus if self.probe_source(s))
        self.db.flush()
        stats = {"focus_sources": len(focus), "reachable": reachable}
        logger.info("hourly_health_inspection", **stats)
        return stats

    # ---------- 主动告警 ----------

    def check_success_rates(self, sources: list[Source]) -> int:
        """源 24h 采集成功率跌破阈值（默认 95%）→ 主动告警（防抖 1h）。返回告警条数。"""
        threshold = self.settings.source_success_rate_alert_threshold
        alerts = 0
        for source in sources:
            fail_rate = self.gov.source_fail_rate(source.id)  # 24h 滑动窗口（settings）
            success_rate = 1.0 - fail_rate
            if success_rate >= threshold:
                continue
            triggered = write_system_alert(
                self.db, self.redis,
                payload={
                    "kind": "source_success_rate_low",
                    "severity": "P1",
                    "source_id": str(source.id),
                    "source_name": source.name,
                    "country_code": source.country_code,
                    "success_rate": round(success_rate, 4),
                    "threshold": threshold,
                    "window_hours": self.settings.source_fail_rate_window_hours,
                },
                debounce_key=f"alert:source_success_rate:{source.id}",
                debounce_seconds=_SUCCESS_RATE_DEBOUNCE_SECONDS,
            )
            if triggered:
                alerts += 1
                logger.warning(
                    "source_success_rate_alert", source_id=str(source.id),
                    success_rate=round(success_rate, 4), threshold=threshold,
                )
        return alerts

    def check_country_coverage(self, sources: list[Source]) -> bool:
        """国家覆盖率跌破阈值（默认 70%）→ P0 主动告警（防抖 6h）。返回是否触发。"""
        threshold = self.settings.country_coverage_alert_threshold
        coverage, covered, uncovered = compute_country_coverage(sources)
        if coverage >= threshold:
            return False
        triggered = write_system_alert(
            self.db, self.redis,
            payload={
                "kind": "country_coverage_low",
                "severity": "P0",
                "coverage": round(coverage, 4),
                "threshold": threshold,
                "covered_countries": covered,
                "uncovered_countries": uncovered,
                "checked_at": datetime.now(UTC).isoformat(),
            },
            debounce_key=_COVERAGE_DEBOUNCE_KEY,
            debounce_seconds=_COVERAGE_DEBOUNCE_SECONDS,
        )
        if triggered:
            logger.warning(
                "country_coverage_alert", coverage=round(coverage, 4),
                threshold=threshold, uncovered=uncovered,
            )
        return triggered
