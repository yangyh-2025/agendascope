"""pipeline_latency_sample 延迟埋点（T2.5）。

published_at→visible_at 逐篇采样落表，按源/通道分桶，支撑延迟看板
（by_channel p95_min 与 >2h 红线违规明细，详细设计 1.16 口径）。
详细设计未给出该表 DDL，0002 迁移为自设计（表内 COMMENT 留口径说明）。

ORM models 包归 Phase 1 基线，本表用 SQLAlchemy Core Table 定义在 nlp 包内，避免越界改动。
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import CHAR, BigInteger, Column, DateTime, Integer, MetaData, String, Table, func, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

metadata = MetaData()

pipeline_latency_sample = Table(
    "pipeline_latency_sample",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("article_id", PG_UUID(as_uuid=True), nullable=False, unique=True),
    Column("source_id", PG_UUID(as_uuid=True), nullable=False),
    Column("channel", String(10), nullable=False),
    Column("country_code", CHAR(2), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    Column("visible_at", DateTime(timezone=True), nullable=False),
    Column("latency_ms", Integer, nullable=False),
    Column("latency_bucket", String(10), nullable=False),
    Column("sampled_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# 分桶边界（分钟）：15-30m 贴近 P95 红线 30min，>2h 即红线违规桶
_BUCKET_EDGES_MIN = (5, 15, 30, 60, 120)
_BUCKET_LABELS = ("<5m", "5-15m", "15-30m", "30-60m", "1-2h", ">2h")


def latency_bucket(latency_ms: int) -> str:
    minutes = latency_ms / 60000
    for edge, label in zip(_BUCKET_EDGES_MIN, _BUCKET_LABELS, strict=False):
        if minutes <= edge:
            return label
    return _BUCKET_LABELS[-1]


def record_sample(
    db: Session,
    article_id: UUID,
    source_id: UUID,
    channel: str,
    country_code: str,
    published_at: datetime,
    visible_at: datetime,
) -> int:
    """写入单篇延迟采样；article_id 唯一 + ON CONFLICT 保证重投递幂等。返回 latency_ms。"""
    latency_ms = max(0, int((visible_at - published_at).total_seconds() * 1000))  # 时钟偏移负值收敛为 0
    stmt = (
        pg_insert(pipeline_latency_sample)
        .values(
            article_id=article_id,
            source_id=source_id,
            channel=channel,
            country_code=country_code,
            published_at=published_at,
            visible_at=visible_at,
            latency_ms=latency_ms,
            latency_bucket=latency_bucket(latency_ms),
        )
        .on_conflict_do_nothing(index_elements=["article_id"])
    )
    db.execute(stmt)
    return latency_ms


def channel_stats(db: Session, since: datetime) -> list[dict]:
    """按通道聚合 P95 延迟（分钟）与样本量，对齐延迟看板 by_channel 口径。"""
    p95 = func.percentile_cont(0.95).within_group(pipeline_latency_sample.c.latency_ms)
    stmt = (
        select(
            pipeline_latency_sample.c.channel,
            (p95 / 60000).label("p95_min"),
            func.count().label("sample"),
        )
        .where(pipeline_latency_sample.c.sampled_at >= since)
        .group_by(pipeline_latency_sample.c.channel)
        .order_by(pipeline_latency_sample.c.channel)
    )
    return [
        {"key": row.channel, "p95_min": round(float(row.p95_min), 2), "sample": int(row.sample)}
        for row in db.execute(stmt).all()
    ]
