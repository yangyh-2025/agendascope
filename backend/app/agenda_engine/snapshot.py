"""AgendaSnapshot 每 15 min 刷新（T3.16，详细设计 2.9 agenda_snapshots DDL）。

数据流：articles + topic_articles + topics → 按 (country_code, topic_id, window_start)
聚合 → 写 agenda_snapshots 表（granularity='hour'）。

显著性得分（salience_score）计算公式：
    score = article_count × (1 + ln(1 + 议题总文章数)) × time_decay × source_diversity
  - article_count：窗内该议题在该国的文章数
  - 议题总文章数：议题历史所有 topic_articles 数（含历史，不限窗内）
  - time_decay：窗内最新文章距 window_end 的小时数衰减（1 / (1 + lag_hours)）
  - source_diversity：窗内该议题不同 source 数 / 窗内文章数（多源并发得分高）

超时与失败降级（详细设计 2.129 + PRD 8.5）：
  - 单次计算 > snapshot_timeout_seconds（默认 300s）：跳过剩余国家当次刷新，
    保留上一版结果（不删除/不覆盖），记 warning
  - 连续 snapshot_failure_alert_threshold（默认 3）次失败：写 alerts 表 P1 告警
  - 单国失败不阻塞其他国（独立 try/except 记 error 后继续）
  - 不抛错（快照是优化非正确性依赖）

情感占比：M3-3 阶段 articles 表无 sentiment 字段（情感分析 Phase 4 接入），
本版本 top_attributes/sentiment 字段以"占位但显式标注"的方式呈现——
sentiment_pos/neu/neg 写 None（NULL 不比 0.33 均匀分布更真实——NULL 表示"未计算"），
由前端"数据待计算"标注。绝不伪造数据。
"""
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.agenda_engine.config import get_agenda_settings
from app.agenda_engine.entity_blacklist import filter_blacklisted
from app.clustering.tokenize import top_keywords
from app.core.logging import get_logger
from app.models.alert import Alert, AlertRule
from app.models.article import Article
from app.models.snapshots import TopicSnapshot as AgendaSnapshot
from app.models.topic import Topic, TopicArticle
from app.services.seed_service import ensure_admin

logger = get_logger("agenda.snapshot")

GRANULARITY_HOUR = "hour"

# 系统内置规则名：快照刷新失败监控（详细设计 2.130 降级行）
SYSTEM_SNAPSHOT_HEALTH_RULE = "系统-快照刷新监控"


def _ensure_snapshot_rule(db: Session) -> AlertRule:
    """系统内置快照监控规则（幂等），快照失败 P1 告警挂靠此规则写入 alerts 表。"""
    rule = db.scalar(select(AlertRule).where(AlertRule.name == SYSTEM_SNAPSHOT_HEALTH_RULE))
    if rule is not None:
        return rule
    admin = ensure_admin(db)
    rule = AlertRule(
        user_id=admin.id,
        name=SYSTEM_SNAPSHOT_HEALTH_RULE,
        country_codes=[],
        keywords=["__snapshot_health__"],
        condition_type="growth_rate",
        condition_value=0,
        notify_channels=["inapp"],
    )
    db.add(rule)
    db.flush()
    return rule


@dataclass(frozen=True)
class TopicSnapshotRow:
    """单 (country, topic, window) 计算结果（写入前）。"""

    topic_id: UUID
    country_code: str
    window_start: datetime
    window_end: datetime
    article_count: int
    salience_score: float
    salience_rank: int  # 在该国内按 score 降序排名（1 起）
    top_attributes: list[str] = field(default_factory=list)
    network_metrics: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RefreshReport:
    """refresh_snapshots 返回报告。"""

    computed_countries: list[str]
    skipped_countries: list[str]
    failed_countries: list[str]
    total_topics: int
    elapsed_seconds: float
    consecutive_failures: int  # 当次失败计数（用于告警判断）
    timeout_exceeded: bool


def _score_topic(
    window_articles: int,
    total_articles: int,
    latest_lag_hours: float,
    distinct_sources: int,
) -> float:
    """显著性得分：窗内文章数 × (1 + ln(1+总数)) × 时间衰减 × 源多样性。"""
    import math
    if window_articles <= 0:
        return 0.0
    diversity = distinct_sources / window_articles if window_articles else 0.0
    time_decay = 1.0 / (1.0 + max(latest_lag_hours, 0.0))
    return float(window_articles * (1.0 + math.log(1.0 + total_articles)) * time_decay * diversity)


def compute_country_snapshots(
    db: Session,
    country_code: str,
    window_start: datetime,
    window_end: datetime,
    redis_client=None,
) -> list[TopicSnapshotRow]:
    """计算单国当窗快照。

    - 拉该国内活跃议题（merged_into IS NULL AND lifecycle_state != 'archived'）
    - 窗内文章（published_at ∈ [window_start, window_end]）按议题分组
    - salience_score 按 _score_topic 计算；salience_rank 按 score 降序排名
    - top_attributes：议题窗内文章标题+正文 top_keywords，经实体黑名单过滤
    - network_metrics：{'size', 'countries', 'distinct_sources', 'latest_lag_hours'}
    """
    # 拉窗内该国所有 (topic_id, article) 行（经 topic_articles 关联）
    stmt = (
        select(
            TopicArticle.topic_id,
            Article.id,
            Article.source_id,
            Article.title,
            Article.content,
            Article.published_at,
        )
        .join(Article, TopicArticle.article_id == Article.id)
        .join(Topic, TopicArticle.topic_id == Topic.id)
        .where(
            Article.country_code == country_code,
            Article.published_at >= window_start,
            Article.published_at <= window_end,
            Topic.merged_into.is_(None),
            Topic.lifecycle_state != "archived",
        )
    )
    rows = db.execute(stmt).all()
    if not rows:
        return []

    # 按议题聚合
    by_topic: dict[UUID, list] = {}
    for row in rows:
        by_topic.setdefault(row.topic_id, []).append(row)

    snapshots: list[TopicSnapshotRow] = []
    for topic_id, articles in by_topic.items():
        window_count = len(articles)
        distinct_sources = len({a.source_id for a in articles})
        latest = max(a.published_at for a in articles)
        latest_lag_hours = max((window_end - latest).total_seconds() / 3600.0, 0.0)
        # 议题历史总文章数（含历史，不限窗内）
        total_articles = int(
            db.scalar(
                select(func.count()).select_from(TopicArticle).where(TopicArticle.topic_id == topic_id)
            ) or 0
        )
        score = _score_topic(window_count, total_articles, latest_lag_hours, distinct_sources)

        # top_attributes：窗内标题+正文 top_keywords，经黑名单过滤
        texts = [f"{a.title}\n{a.content or ''}" for a in articles]
        top_words = top_keywords(texts, limit=10)
        if redis_client is not None:
            top_words = filter_blacklisted(top_words, redis_client)

        # network_metrics：规模/国家数/源数/最新时滞
        country_count = int(
            db.scalar(
                select(func.count(func.distinct(Article.country_code)))
                .select_from(TopicArticle)
                .join(Article, TopicArticle.article_id == Article.id)
                .where(TopicArticle.topic_id == topic_id)
            ) or 0
        )
        network_metrics = {
            "size": total_articles,
            "countries": country_count,
            "distinct_sources": distinct_sources,
            "latest_lag_hours": round(latest_lag_hours, 2),
        }

        snapshots.append(TopicSnapshotRow(
            topic_id=topic_id,
            country_code=country_code,
            window_start=window_start,
            window_end=window_end,
            article_count=window_count,
            salience_score=score,
            salience_rank=0,  # 后填
            top_attributes=top_words[:10],
            network_metrics=network_metrics,
        ))

    # 按 score 降序排名（同分按 article_count 降序，再按 topic_id 字典序兜底确定性）
    snapshots.sort(key=lambda s: (-s.salience_score, -s.article_count, str(s.topic_id)))
    return [
        TopicSnapshotRow(
            topic_id=s.topic_id, country_code=s.country_code,
            window_start=s.window_start, window_end=s.window_end,
            article_count=s.article_count, salience_score=s.salience_score,
            salience_rank=i + 1, top_attributes=s.top_attributes,
            network_metrics=s.network_metrics,
        )
        for i, s in enumerate(snapshots)
    ]


def _upsert_snapshots(db: Session, snapshots: list[TopicSnapshotRow]) -> int:
    """UPSERT agenda_snapshots：UK(country_code, topic_id, window_start, granularity) 冲突更新。"""
    if not snapshots:
        return 0
    values = [
        {
            "country_code": s.country_code,
            "topic_id": s.topic_id,
            "window_start": s.window_start,
            "window_end": s.window_end,
            "granularity": GRANULARITY_HOUR,
            "article_count": s.article_count,
            "salience_score": s.salience_score,
            "salience_rank": s.salience_rank,
            "sentiment_pos": None,  # M3-3 阶段 articles 无 sentiment 字段，留 NULL 不伪造
            "sentiment_neu": None,
            "sentiment_neg": None,
            "top_attributes": {"keywords": s.top_attributes, "sentiment_placeholder": True},
            "network_metrics": s.network_metrics,
        }
        for s in snapshots
    ]
    stmt = pg_insert(AgendaSnapshot).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["country_code", "topic_id", "window_start", "granularity"],
        set_={
            "window_end": stmt.excluded.window_end,
            "article_count": stmt.excluded.article_count,
            "salience_score": stmt.excluded.salience_score,
            "salience_rank": stmt.excluded.salience_rank,
            "top_attributes": stmt.excluded.top_attributes,
            "network_metrics": stmt.excluded.network_metrics,
        },
    )
    db.execute(stmt)
    db.flush()
    return len(snapshots)


def refresh_snapshots(
    db: Session,
    redis_client=None,
    *,
    now: datetime | None = None,
    consecutive_failures_state: dict | None = None,
) -> RefreshReport:
    """主入口：按 snapshot_interval_minutes 周期调用一次。

    - 取近 snapshot_window_hours 窗（window_end=now, window_start=now-24h）
    - 拉覆盖国家列表（articles.country_code distinct 近 24h，最多 snapshot_max_countries）
    - 逐国 compute_country_snapshots（每国独立 try/except）
    - 单次总耗时 > snapshot_timeout_seconds（默认 300s）：跳过剩余国家当次刷新，
      保留上一版结果（已 upsert 的部分保留，未处理的国家不动——下一周期重试）
    - 连续 snapshot_failure_alert_threshold（默认 3）次失败：写 alerts P1 告警
    - 返回 RefreshReport
    """
    import time

    settings = get_agenda_settings()
    now = now or datetime.now(UTC)
    window_end = now
    window_start = now - timedelta(hours=settings.snapshot_window_hours)
    start = time.monotonic()

    # 跟踪连续失败计数（进程外传递以支持跨轮）
    state = consecutive_failures_state if consecutive_failures_state is not None else {}
    consecutive_failures = int(state.get("consecutive_failures", 0))

    # 拉覆盖国家列表
    countries = list(
        db.scalars(
            select(Article.country_code)
            .where(Article.published_at >= window_start)
            .group_by(Article.country_code)
            .order_by(func.count(Article.id).desc())
            .limit(settings.snapshot_max_countries)
        ).all()
    )

    computed: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    total_topics = 0
    timeout_exceeded = False

    for country in countries:
        elapsed = time.monotonic() - start
        if elapsed > settings.snapshot_timeout_seconds:
            # 超时：跳过剩余国家，保留上版结果
            skipped.extend([c for c in countries if c not in computed and c not in failed])
            timeout_exceeded = True
            logger.warning(
                "snapshot_timeout",
                elapsed_seconds=round(elapsed, 1),
                remaining_countries=len(countries) - len(computed) - len(failed),
            )
            break
        try:
            snapshots = compute_country_snapshots(db, country, window_start, window_end, redis_client)
            _upsert_snapshots(db, snapshots)
            db.commit()
            computed.append(country)
            total_topics += len(snapshots)
        except Exception as exc:  # noqa: BLE001 单国失败不阻塞其他国
            db.rollback()
            failed.append(country)
            logger.error("snapshot_country_fail", country=country, error=str(exc)[:300])

    # 失败计数与告警
    if failed:
        consecutive_failures += 1
    else:
        consecutive_failures = 0
    state["consecutive_failures"] = consecutive_failures

    if consecutive_failures >= settings.snapshot_failure_alert_threshold:
        _write_snapshot_alert(db, consecutive_failures, failed)
        consecutive_failures = 0  # 已告警后重置，避免每轮重复告警
        state["consecutive_failures"] = 0

    elapsed_total = time.monotonic() - start
    return RefreshReport(
        computed_countries=computed,
        skipped_countries=skipped,
        failed_countries=failed,
        total_topics=total_topics,
        elapsed_seconds=round(elapsed_total, 2),
        consecutive_failures=consecutive_failures,
        timeout_exceeded=timeout_exceeded,
    )


def _write_snapshot_alert(db: Session, consecutive_failures: int, failed_countries: list[str]) -> None:
    """连续 N 次失败写 alerts P1 告警（系统规则 + 管理员收件，不抛错）。"""
    try:
        admin = ensure_admin(db)
        rule = _ensure_snapshot_rule(db)
        db.add(Alert(
            rule_id=rule.id,
            user_id=admin.id,
            payload={
                "kind": "snapshot_refresh_failure",
                "severity": "P1",
                "consecutive_failures": consecutive_failures,
                "failed_countries": failed_countries,
                "message": f"快照刷新连续 {consecutive_failures} 次失败（涉及国家：{','.join(failed_countries)}）",
            },
        ))
        db.flush()
    except Exception as exc:  # noqa: BLE001 告警失败不阻塞主流程
        logger.error("snapshot_alert_fail", error=str(exc)[:200])


__all__ = [
    "RefreshReport",
    "TopicSnapshotRow",
    "compute_country_snapshots",
    "refresh_snapshots",
]
