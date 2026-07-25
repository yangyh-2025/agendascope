"""T3.2 议题生命周期状态机单元测试：合法转移校验、规模推进、消亡扫描。

构造真实 SQLAlchemy session（db fixture）与真实 Topic 行验证 sweep_archived 行为，
不 Mock 数据库。时间字段直接构造（datetime.now(UTC) ± timedelta），不依赖系统时钟 sleep。
"""
from datetime import UTC, datetime, timedelta

import pytest

from app.agenda_engine.lifecycle import (
    active_topic_ids,
    advance_for_size,
    can_transition,
    sweep_archived,
    topic_size,
)
from app.models.article import Article
from app.models.topic import Topic, TopicArticle
from tests.conftest import make_source


def _make_topic(db, **kwargs) -> Topic:
    """构造最小可用 Topic（字段与该模型 nullable=False 约束对齐）。"""
    defaults = {
        "name": "测试议题",
        "name_auto": "测试议题",
        "naming_method": "ctfidf_fallback",
        "cluster_method": "agglomerative",
        "keywords": ["测试"],
        "country_scope": ["CN"],
        "lifecycle_state": "nascent",
    }
    defaults.update(kwargs)
    topic = Topic(**defaults)
    db.add(topic)
    db.flush()
    return topic


class TestCanTransition:
    @pytest.mark.parametrize(
        "current,target,expected",
        [
            ("nascent", "forming", True),
            ("nascent", "archived", True),
            ("nascent", "confirmed", False),  # 跨级不允许
            ("forming", "confirmed", True),
            ("forming", "archived", True),
            ("forming", "nascent", False),  # 只前进不后退
            ("confirmed", "evolving", True),
            ("confirmed", "archived", True),
            ("confirmed", "forming", False),
            ("evolving", "forming", True),
            ("evolving", "confirmed", True),
            ("evolving", "archived", True),
            ("evolving", "nascent", False),
            ("archived", "forming", False),  # archived 终态
            ("archived", "archived", False),
        ],
    )
    def test_transitions(self, current, target, expected):
        assert can_transition(current, target) is expected

    def test_unknown_state(self):
        assert can_transition("unknown", "forming") is False


class TestAdvanceForSize:
    def test_nascent_stays_at_size_1(self):
        assert advance_for_size("nascent", 1, confirmed_min_size=10) == "nascent"

    def test_nascent_to_forming_at_size_2(self):
        assert advance_for_size("nascent", 2, confirmed_min_size=10) == "forming"

    def test_forming_to_confirmed_at_threshold(self):
        assert advance_for_size("forming", 10, confirmed_min_size=10) == "confirmed"

    def test_confirmed_stays_confirmed_above_threshold(self):
        assert advance_for_size("confirmed", 15, confirmed_min_size=10) == "confirmed"

    def test_only_forward_no_backward(self):
        # forming 议题规模因重聚类下降到 1：不后退到 nascent
        assert advance_for_size("forming", 1, confirmed_min_size=10) == "forming"

    def test_evolving_not_driven_by_size(self):
        # evolving 由归并/分裂流程显式维护，规模变化不自动改它
        assert advance_for_size("evolving", 100, confirmed_min_size=10) == "evolving"
        assert advance_for_size("evolving", 1, confirmed_min_size=10) == "evolving"

    def test_archived_terminal(self):
        assert advance_for_size("archived", 100, confirmed_min_size=10) == "archived"


class TestSweepArchived:
    def test_archives_stale_topic(self, db):
        stale_time = datetime.now(UTC) - timedelta(days=10)
        topic = _make_topic(db, lifecycle_state="confirmed", last_seen_at=stale_time)
        archived = sweep_archived(db, archive_after_days=7)
        assert topic.id in archived
        db.refresh(topic)
        assert topic.lifecycle_state == "archived"

    def test_keeps_active_topic(self, db):
        recent_time = datetime.now(UTC) - timedelta(days=3)
        topic = _make_topic(db, lifecycle_state="forming", last_seen_at=recent_time)
        archived = sweep_archived(db, archive_after_days=7)
        assert topic.id not in archived
        db.refresh(topic)
        assert topic.lifecycle_state == "forming"

    def test_skips_merged_into_topic(self, db):
        # merged_into 非空的源议题由归并流程置 evolving，sweeper 不再触碰
        stale_time = datetime.now(UTC) - timedelta(days=30)
        survivor = _make_topic(db, name="存活议题", last_seen_at=datetime.now(UTC))
        merged = _make_topic(
            db, name="已归并源议题", lifecycle_state="evolving",
            last_seen_at=stale_time, merged_into=survivor.id,
        )
        archived = sweep_archived(db, archive_after_days=7)
        assert merged.id not in archived
        db.refresh(merged)
        assert merged.lifecycle_state == "evolving"

    def test_skips_human_locked_topic(self, db):
        # 人工锁定字段议题不自动消亡（尊重人工结论）
        stale_time = datetime.now(UTC) - timedelta(days=30)
        topic = _make_topic(
            db, lifecycle_state="confirmed", last_seen_at=stale_time,
            human_locked_fields=["name"],
        )
        archived = sweep_archived(db, archive_after_days=7)
        assert topic.id not in archived
        db.refresh(topic)
        assert topic.lifecycle_state == "confirmed"

    def test_skips_already_archived(self, db):
        stale_time = datetime.now(UTC) - timedelta(days=30)
        topic = _make_topic(db, lifecycle_state="archived", last_seen_at=stale_time)
        archived = sweep_archived(db, archive_after_days=7)
        assert topic.id not in archived

    def test_uses_config_default_days(self, db, monkeypatch):
        # 不传 archive_after_days 时读 AgendaSettings.lifecycle_archive_days
        from app.agenda_engine import lifecycle as lifecycle_module
        from app.agenda_engine.config import get_agenda_settings
        get_agenda_settings.cache_clear()
        monkeypatch.setenv("AGENDA_LIFECYCLE_ARCHIVE_DAYS", "3")
        get_agenda_settings.cache_clear()
        try:
            stale_time = datetime.now(UTC) - timedelta(days=5)
            topic = _make_topic(db, lifecycle_state="forming", last_seen_at=stale_time)
            archived = lifecycle_module.sweep_archived(db)
            assert topic.id in archived
        finally:
            get_agenda_settings.cache_clear()


class TestHelpers:
    def test_active_topic_ids_excludes_archived_and_merged(self, db):
        active = _make_topic(db, name="活跃")
        _make_topic(db, name="归档", lifecycle_state="archived")
        survivor = _make_topic(db, name="存活2")
        _make_topic(db, name="已归并", merged_into=survivor.id)
        ids = active_topic_ids(db)
        assert active.id in ids
        assert survivor.id in ids
        assert all(
            db.get(Topic, tid).lifecycle_state != "archived" and db.get(Topic, tid).merged_into is None
            for tid in ids
        )

    def test_topic_size_counts_assignments(self, db):
        topic = _make_topic(db)
        source = make_source(db)
        for i in range(3):
            article = Article(
                source_id=source.id,
                url=f"https://example.com/a{i}",
                url_hash=f"hash-{i}",
                title=f"文章 {i}",
                language="zh",
                country_code="CN",
                published_at=datetime.now(UTC),
            )
            db.add(article)
            db.flush()
            db.add(TopicArticle(topic_id=topic.id, article_id=article.id, weight=1.0))
        db.flush()
        assert topic_size(db, topic.id) == 3
