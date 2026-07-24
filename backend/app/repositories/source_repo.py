"""sources 数据访问。"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Float, cast, func, select
from sqlalchemy.orm import Session

from app.models.collection import JOB_SUCCESS, JOB_TEMP_FAIL, JOB_PERM_FAIL, CollectionJob
from app.models.source import Source


class SourceRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, source_id: uuid.UUID) -> Source | None:
        return self.db.get(Source, source_id)

    def get_by_feed_url(self, feed_url: str) -> Source | None:
        return self.db.scalar(select(Source).where(Source.feed_url == feed_url))

    def list(self, country_code=None, status=None, collect_mode=None, is_custom=None,
             keyword=None, sort="audience_weight_desc", page=1, page_size=20):
        stmt = select(Source)
        if country_code:
            stmt = stmt.where(Source.country_code == country_code)
        if status:
            stmt = stmt.where(Source.status == status)
        if collect_mode:
            stmt = stmt.where(Source.collect_mode == collect_mode)
        if is_custom is not None:
            stmt = stmt.where(Source.is_custom == is_custom)
        if keyword:
            like = f"%{keyword}%"
            stmt = stmt.where(Source.name.ilike(like) | Source.name_zh.ilike(like))
        total = self.db.scalar(select(func.count()).select_from(stmt.subquery()))
        if sort == "name":
            stmt = stmt.order_by(Source.name)
        elif sort == "last_success_at":
            stmt = stmt.order_by(Source.last_success_at.desc().nulls_last())
        else:
            stmt = stmt.order_by(Source.audience_weight.desc().nulls_last())
        items = self.db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all()
        return total, items

    def health_24h(self, source_id) -> dict:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        row = self.db.execute(
            select(
                func.count(),
                func.count().filter(CollectionJob.status == JOB_SUCCESS),
                func.coalesce(func.sum(CollectionJob.articles_new), 0),
                func.avg(
                    cast(CollectionJob.latency_stats["avg_delay_min"].astext, Float)
                ).filter(CollectionJob.latency_stats.isnot(None)),
            ).where(CollectionJob.source_id == source_id, CollectionJob.scheduled_at >= since)
        ).one()
        total, success, articles, avg_latency = row
        return {
            "success_rate": round(success / total, 4) if total else None,
            "articles_24h": int(articles or 0),
            "avg_latency_min": round(float(avg_latency), 2) if avg_latency is not None else None,
        }

    def coverage_by_country(self) -> list[dict]:
        rows = self.db.execute(
            select(
                Source.country_code,
                func.count(),
                func.count().filter(Source.status == "active"),
                func.coalesce(func.sum(Source.audience_weight).filter(Source.status == "active"), 0),
                func.count().filter(Source.coverage_confidence == "high").filter(Source.status == "active"),
            ).where(Source.is_custom.is_(False)).group_by(Source.country_code)
        ).all()
        return [
            {
                "country_code": r[0],
                "source_count": r[1],
                "active_count": r[2],
                "total_audience_share": round(float(r[3]) / 100, 4),
                "high_confidence_sources": r[4],
            }
            for r in rows
        ]
