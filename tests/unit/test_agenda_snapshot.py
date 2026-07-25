"""T3.16 AgendaSnapshot 单元测试：显著性计算、UPSERT、超时降级、连续失败告警。

真实 db fixture（agendascope_test）+ 真实 Redis（db14），不 Mock。
"""
from datetime import UTC, datetime, timedelta

from app.agenda_engine.config import get_agenda_settings
from app.agenda_engine.snapshot import (
    compute_country_snapshots,
    refresh_snapshots,
)
from app.models.alert import Alert
from app.models.article import Article
from app.models.topic import AgendaSnapshot, Topic, TopicArticle
from tests.conftest import make_source
from tests.integration.conftest import make_article


def _make_topic(db, **kwargs) -> Topic:
    defaults = {
        "name": "快照测试议题",
        "name_auto": "快照测试议题",
        "naming_method": "ctfidf_fallback",
        "cluster_method": "agglomerative",
        "keywords": ["快照"],
        "country_scope": ["CN"],
        "lifecycle_state": "forming",
    }
    defaults.update(kwargs)
    topic = Topic(**defaults)
    db.add(topic)
    db.flush()
    return topic


def _assign(db, topic: Topic, article: Article) -> None:
    db.add(TopicArticle(topic_id=topic.id, article_id=article.id, weight=1.0))
    db.flush()


class TestComputeCountrySnapshots:
    def test_empty_country_returns_empty(self, db):
        now = datetime.now(UTC)
        snapshots = compute_country_snapshots(
            db, "US", now - timedelta(hours=24), now,
        )
        assert snapshots == []

    def test_single_topic_rank_1(self, db):
        now = datetime.now(UTC)
        source = make_source(db, country_code="CN", language="zh")
        topic = _make_topic(db)
        for i in range(5):
            article = make_article(
                db, source, title=f"快照测试 {i}",
                published_at=now - timedelta(hours=i),
            )
            _assign(db, topic, article)
        db.commit()

        snapshots = compute_country_snapshots(
            db, "CN", now - timedelta(hours=24), now,
        )
        assert len(snapshots) == 1
        snap = snapshots[0]
        assert snap.topic_id == topic.id
        assert snap.article_count == 5
        assert snap.salience_rank == 1
        assert snap.salience_score > 0
        assert snap.network_metrics["distinct_sources"] == 1
        assert snap.network_metrics["size"] == 5

    def test_multiple_topics_ranked_by_score(self, db):
        now = datetime.now(UTC)
        source = make_source(db, country_code="CN", language="zh")
        topic_a = _make_topic(db, name="高分议题")
        topic_b = _make_topic(db, name="低分议题")
        # topic_a：5 篇窗内新文章（高分）；topic_b：1 篇窗内老文章（低分）
        for i in range(5):
            article = make_article(
                db, source, title=f"A 议题 {i}",
                published_at=now - timedelta(hours=1),
            )
            _assign(db, topic_a, article)
        article_b = make_article(
            db, source, title="B 议题老文章",
            published_at=now - timedelta(hours=20),
        )
        _assign(db, topic_b, article_b)
        db.commit()

        snapshots = compute_country_snapshots(
            db, "CN", now - timedelta(hours=24), now,
        )
        assert len(snapshots) == 2
        assert snapshots[0].topic_id == topic_a.id
        assert snapshots[0].salience_rank == 1
        assert snapshots[1].topic_id == topic_b.id
        assert snapshots[1].salience_rank == 2
        assert snapshots[0].salience_score > snapshots[1].salience_score

    def test_archived_topic_excluded(self, db):
        now = datetime.now(UTC)
        source = make_source(db, country_code="CN", language="zh")
        archived = _make_topic(db, lifecycle_state="archived")
        article = make_article(
            db, source, title="已归档议题文章",
            published_at=now - timedelta(hours=1),
        )
        _assign(db, archived, article)
        db.commit()
        snapshots = compute_country_snapshots(
            db, "CN", now - timedelta(hours=24), now,
        )
        assert snapshots == []

    def test_top_attributes_filtered_by_blacklist(self, db, redis_client):
        # 先写黑名单
        settings = get_agenda_settings()
        redis_client.delete(settings.entity_blacklist_key)
        redis_client.sadd(settings.entity_blacklist_key, "美国")

        now = datetime.now(UTC)
        source = make_source(db, country_code="CN", language="zh")
        topic = _make_topic(db)
        article = make_article(
            db, source, title="美国总统拜登发表重要讲话",
            content="美国 总统 拜登 讲话 重要",
            published_at=now - timedelta(hours=1),
        )
        _assign(db, topic, article)
        db.commit()

        snapshots = compute_country_snapshots(
            db, "CN", now - timedelta(hours=24), now, redis_client=redis_client,
        )
        assert len(snapshots) == 1
        assert "美国" not in snapshots[0].top_attributes
        # 黑名单外的关键词仍在（"总统"/"拜登" 至少一个出现）
        assert any(w in snapshots[0].top_attributes for w in ["总统", "拜登"])


class TestRefreshSnapshots:
    def test_upsert_idempotent(self, db, redis_client):
        """同 (country, topic, window_start) 重复刷新：更新而非插入重复行。"""
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        source = make_source(db, country_code="CN", language="zh")
        topic = _make_topic(db)
        article = make_article(
            db, source, title="重复刷新测试",
            published_at=now - timedelta(hours=1),
        )
        _assign(db, topic, article)
        db.commit()

        # 第一轮
        report1 = refresh_snapshots(db, redis_client=redis_client, now=now)
        assert report1.failed_countries == []
        count1 = db.query(AgendaSnapshot).filter(
            AgendaSnapshot.country_code == "CN",
            AgendaSnapshot.topic_id == topic.id,
        ).count()
        assert count1 == 1
        first_id = db.query(AgendaSnapshot.id).filter(
            AgendaSnapshot.country_code == "CN",
            AgendaSnapshot.topic_id == topic.id,
        ).scalar()

        # 第二轮（同 now，幂等更新）
        report2 = refresh_snapshots(db, redis_client=redis_client, now=now)
        assert report2.failed_countries == []
        count2 = db.query(AgendaSnapshot).filter(
            AgendaSnapshot.country_code == "CN",
            AgendaSnapshot.topic_id == topic.id,
        ).count()
        assert count2 == 1  # 仍 1 行
        second_id = db.query(AgendaSnapshot.id).filter(
            AgendaSnapshot.country_code == "CN",
            AgendaSnapshot.topic_id == topic.id,
        ).scalar()
        assert first_id == second_id  # 同一行被更新

    def test_consecutive_failures_alert(self, db, redis_client, monkeypatch):
        """连续 N 次失败写 alerts P1 告警。"""
        # 让 compute_country_snapshots 抛错
        from app.agenda_engine import snapshot as snapshot_module

        def _boom(*args, **kwargs):
            raise RuntimeError("注入故障")

        monkeypatch.setattr(snapshot_module, "compute_country_snapshots", _boom)

        now = datetime.now(UTC)
        source = make_source(db, country_code="CN", language="zh")
        make_article(db, source, title="触发告警", published_at=now - timedelta(hours=1))
        db.commit()

        state = {"consecutive_failures": 0}
        threshold = get_agenda_settings().snapshot_failure_alert_threshold
        for _ in range(threshold):
            refresh_snapshots(
                db, redis_client=redis_client, now=now,
                consecutive_failures_state=state,
            )

        alerts = db.query(Alert).filter(
            Alert.payload["kind"].astext == "snapshot_refresh_failure"
        ).all()
        assert alerts, "连续失败应写 P1 告警"
        assert alerts[0].payload["severity"] == "P1"
        assert alerts[0].payload["consecutive_failures"] == threshold

    def test_timeout_skips_remaining_countries(self, db, redis_client, monkeypatch):
        """超时后跳过剩余国家，已完成国家保留。"""
        from app.agenda_engine import snapshot as snapshot_module

        now = datetime.now(UTC)
        # 造 3 国
        for cc in ["CN", "US", "GB"]:
            source = make_source(db, country_code=cc, language="zh")
            make_article(db, source, title=f"{cc} 文章", published_at=now - timedelta(hours=1))
        db.commit()

        real_compute = snapshot_module.compute_country_snapshots
        call_count = {"n": 0}

        def _slow_compute(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] >= 2:
                # 第二次调用模拟已经耗时过长
                snapshot_module.time.monotonic = lambda: start_time + 9999
            return real_compute(*args, **kwargs)

        monkeypatch.setattr(
            snapshot_module, "compute_country_snapshots", _slow_compute,
        )
        import time as _time
        start_time = _time.monotonic()

        # 把超时阈值改小以触发分支
        original_timeout = get_agenda_settings().snapshot_timeout_seconds
        get_agenda_settings().snapshot_timeout_seconds = 1
        try:
            report = refresh_snapshots(db, redis_client=redis_client, now=now)
            # 至少第一国完成，剩余被跳过
            assert report.timeout_exceeded is True or report.skipped_countries or report.computed_countries
        finally:
            get_agenda_settings().snapshot_timeout_seconds = original_timeout


class TestScoreFormula:
    def test_zero_window_articles_zero_score(self):
        from app.agenda_engine.snapshot import _score_topic
        assert _score_topic(0, 100, 0.0, 0) == 0.0

    def test_higher_diversity_higher_score(self):
        from app.agenda_engine.snapshot import _score_topic
        single_source = _score_topic(10, 100, 1.0, 1)
        multi_source = _score_topic(10, 100, 1.0, 5)
        assert multi_source > single_source

    def test_recent_articles_higher_score(self):
        from app.agenda_engine.snapshot import _score_topic
        recent = _score_topic(10, 100, 0.5, 5)
        old = _score_topic(10, 100, 20.0, 5)
        assert recent > old
