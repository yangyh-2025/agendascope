"""T3.16 快照集成测试：真实 PG + Redis 完整链路 + snapshot_worker 单轮。"""
from datetime import UTC, datetime, timedelta

import pytest

from app.agenda_engine.snapshot import refresh_snapshots
from app.models.snapshots import TopicSnapshot as AgendaSnapshot
from app.models.topic import Topic, TopicArticle
from app.worker.snapshot_worker import SnapshotWorker
from tests.conftest import make_source
from tests.integration.conftest import make_article

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _make_topic(db, **kwargs) -> Topic:
    defaults = {
        "name": "集成测试议题",
        "name_auto": "集成测试议题",
        "naming_method": "ctfidf_fallback",
        "cluster_method": "agglomerative",
        "keywords": ["集成"],
        "country_scope": ["CN"],
        "lifecycle_state": "forming",
    }
    defaults.update(kwargs)
    topic = Topic(**defaults)
    db.add(topic)
    db.flush()
    return topic


async def test_full_pipeline_multi_country(db, redis_client):
    """真实建 3 国 × 多议题 × 多文章 → refresh_snapshots → agenda_snapshots 写入正确。"""
    now = datetime.now(UTC)
    # 造 3 国 × 2 议题 × 5 文章
    for cc in ["CN", "US", "GB"]:
        source = make_source(db, country_code=cc, language="zh")
        topic = _make_topic(db, name=f"{cc} 议题", country_scope=[cc])
        for i in range(5):
            article = make_article(
                db, source, title=f"{cc} 报道 {i}",
                published_at=now - timedelta(hours=i),
            )
            db.add(TopicArticle(topic_id=topic.id, article_id=article.id, weight=1.0))
    db.commit()

    report = refresh_snapshots(db, redis_client=redis_client, now=now)
    assert set(report.computed_countries) == {"CN", "US", "GB"}
    assert report.failed_countries == []
    assert report.total_topics == 3

    # 验证 agenda_snapshots 表
    snapshots = db.query(AgendaSnapshot).filter(
        AgendaSnapshot.country_code.in_(["CN", "US", "GB"])
    ).all()
    assert len(snapshots) == 3
    for snap in snapshots:
        assert snap.article_count == 5
        assert snap.salience_rank == 1
        assert snap.salience_score > 0


async def test_snapshot_worker_once(db, redis_client):
    """snapshot_worker.maybe_refresh() 单轮真实触发。"""
    now = datetime.now(UTC)
    source = make_source(db, country_code="CN", language="zh")
    topic = _make_topic(db)
    article = make_article(
        db, source, title="worker 测试",
        published_at=now - timedelta(hours=1),
    )
    db.add(TopicArticle(topic_id=topic.id, article_id=article.id, weight=1.0))
    db.commit()

    worker = SnapshotWorker(redis_client=redis_client)
    assert worker.maybe_refresh() is True
    # 第二轮立即调用：间隔未到不再触发
    assert worker.maybe_refresh() is False
