"""T3.10 统计佐证集成测试：真实建 topic + 真实 articles + 真实统计计算。

场景：
  1) 100+ 篇真实文章（origin 第 1 天报道、follower_A 第 3 天、follower_B 第 5 天脉冲）
     → compute_stats_evidence 返回完整 StatsEvidence，xcorr 显著且 lag 合理
  2) 50 篇文章（<100）→ insufficient_data=True 且 rejection_reason 含样本量
"""
import uuid
from datetime import UTC, datetime, timedelta

from app.agenda_engine.stats_evidence import StatsEvidence, compute_stats_evidence
from app.models.article import Article
from app.models.topic import Topic, TopicArticle
from tests.conftest import make_source


def _make_topic(db, **kwargs) -> Topic:
    defaults = {
        "name": "集成测试议题",
        "name_auto": "集成测试议题",
        "naming_method": "ctfidf_fallback",
        "cluster_method": "agglomerative",
        "keywords": ["集成"],
        "country_scope": ["US", "GB", "DE"],
        "lifecycle_state": "confirmed",
    }
    defaults.update(kwargs)
    topic = Topic(**defaults)
    db.add(topic)
    db.flush()
    return topic


def _seed(db, topic, source, *, base_now: datetime, plan: list[tuple[str, int]]):
    for country, days_ago in plan:
        published = base_now - timedelta(days=days_ago)
        article = Article(
            source_id=source.id,
            url=f"https://example.com/{uuid.uuid4().hex}",
            url_hash=uuid.uuid4().hex * 2,
            title=f"集成 {uuid.uuid4().hex[:8]}",
            language="en",
            country_code=country,
            published_at=published,
        )
        db.add(article)
        db.flush()
        db.add(TopicArticle(topic_id=topic.id, article_id=article.id, weight=1.0))
    db.flush()


class TestStatsEvidenceIntegration:
    def test_full_evidence_with_100_articles(self, db):
        """100+ 篇：origin 第 25/20/15/10/5 天脉冲（每天 10 篇），
        follower_A 滞后 2 天同形态、follower_B 滞后 4 天同形态。
        → xcorr 显著且 lag 与 follower_A/B 平均对齐；qap/granger 完成计算不抛异常。
        """
        base_now = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
        source = make_source(db, country_code="US")
        topic = _make_topic(db)

        plan: list[tuple[str, int]] = []
        # origin US 在第 25/20/15/10/5 天前各 8 篇（共 40）
        for days_ago in (25, 20, 15, 10, 5):
            for _ in range(8):
                plan.append(("US", days_ago))
        # follower GB 滞后 2 天：第 23/18/13/8/3 天前各 8 篇（共 40）
        for days_ago in (23, 18, 13, 8, 3):
            for _ in range(8):
                plan.append(("GB", days_ago))
        # follower DE 滞后 4 天：第 21/16/11/6/1 天前各 8 篇（共 40）
        for days_ago in (21, 16, 11, 6, 1):
            for _ in range(8):
                plan.append(("DE", days_ago))
        _seed(db, topic, source, base_now=base_now, plan=plan)

        evidence = compute_stats_evidence(
            db, topic.id, "US", ["GB", "DE"], window_days=30, now=base_now,
        )
        assert isinstance(evidence, StatsEvidence)
        assert evidence.insufficient_data is False
        assert evidence.article_count == 120
        # xcorr 必然计算且显著（脉冲形态高度对齐）
        assert evidence.xcorr is not None
        assert evidence.xcorr.significant is True
        assert evidence.xcorr.best_lag_days in (2, 3, 4)  # 两 follower 平均对齐窗口
        # qap/granger 至少完成计算（允许 None 但不允许抛异常）
        if evidence.qap is not None:
            assert 0.0 <= evidence.qap.p_value <= 1.0
            assert evidence.qap.permutations > 0
        if evidence.granger is not None:
            assert 0.0 <= evidence.granger.p_value <= 1.0
            assert 1 <= evidence.granger.best_lag_days <= 7

    def test_insufficient_50_articles(self, db):
        """50 篇 → insufficient_data=True，rejection_reason 含样本量，所有检验 None。"""
        base_now = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
        source = make_source(db, country_code="US")
        topic = _make_topic(db)

        plan: list[tuple[str, int]] = []
        for days_ago in (10, 8, 6, 4, 2):
            for _ in range(5):
                plan.append(("US", days_ago))
        for days_ago in (9, 7, 5, 3, 1):
            for _ in range(5):
                plan.append(("GB", days_ago))
        _seed(db, topic, source, base_now=base_now, plan=plan)

        evidence = compute_stats_evidence(
            db, topic.id, "US", ["GB"], window_days=30, now=base_now,
        )
        assert evidence.insufficient_data is True
        assert evidence.article_count == 50
        assert evidence.xcorr is None
        assert evidence.granger is None
        assert evidence.qap is None
        assert evidence.rejection_reason is not None
        assert "数据量不足" in evidence.rejection_reason
        assert "50" in evidence.rejection_reason
