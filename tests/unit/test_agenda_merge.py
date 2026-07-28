"""T3.3 次日自动归并单元测试：手工构造单位向量控制 cosine 相似度，禁 Mock。

向量构造思路（与 test_agenda_echo.py 相同）：
  - 基准向量 e1 = [1, 0, ..., 0]
  - 与 e1 余弦为 s 的向量：v = [s, sqrt(1-s^2), 0, ..., 0]（已 L2 归一化）

候选集 C：merged_into IS NULL AND lifecycle_state='nascent' AND first_seen_at 近 24h
档案集 D：merged_into IS NULL AND lifecycle_state != 'archived' AND last_seen_at 近 30d

测试覆盖：
  1. 候选为空 → 返回空 merge_report
  2. 高相似度（≥0.85）候选归并：merged_into 设置、topic_articles 迁移、revision_log 留痕、
     target centroid/country_scope 更新
  3. 低相似度（<0.85）→ 保留新 topic_id，归并不发生
  4. no_merge_with 阻止归并：高相似度但 (c, target) ∈ no_merge_pairs → 跳过且
     记录到 skipped_no_merge
  5. human_locked_fields 含 'merged_into' 的源议题不自动归并
  6. 候选已 archived 不在档案池（archived target 不参与比对）
"""
import math
import uuid
from datetime import UTC, datetime, timedelta

from app.agenda_engine.merge import MergeReport, nextday_merge
from app.models.article import Article
from app.models.topic import Topic, TopicArticle
from tests.conftest import make_source

DIM = 768


def _unit(dim_index: int) -> list[float]:
    v = [0.0] * DIM
    v[dim_index] = 1.0
    return v


def _vec_with_cosine(target_cosine: float, base_dim: int = 0, orthogonal_dim: int = 1) -> list[float]:
    v = [0.0] * DIM
    v[base_dim] = target_cosine
    v[orthogonal_dim] = math.sqrt(max(1.0 - target_cosine * target_cosine, 0.0))
    return v


def _make_topic(db, **kwargs) -> Topic:
    """构造最小可用 Topic（沿用 test_agenda_lifecycle._make_topic 风格）。"""
    now = datetime.now(UTC)
    defaults = {
        "name": "测试议题",
        "name_auto": "测试议题",
        "naming_method": "ctfidf_fallback",
        "cluster_method": "agglomerative",
        "keywords": ["测试"],
        "country_scope": ["US"],
        "lifecycle_state": "nascent",
        "first_seen_at": now,
        "last_seen_at": now,
    }
    defaults.update(kwargs)
    topic = Topic(**defaults)
    db.add(topic)
    db.flush()
    return topic


def _persist_article(db, source, **overrides) -> Article:
    """真实建 Article 行（与 test_agenda_echo 一致）。"""
    defaults = {
        "id": uuid.uuid4(),
        "source_id": source.id,
        "url": f"https://example.com/{uuid.uuid4().hex}",
        "url_hash": uuid.uuid4().hex.ljust(64, "0")[:64],
        "title": "test title",
        "language": "en",
        "published_at": datetime.now(UTC),
        "country_code": "US",
        "embedding": _unit(0),
        "is_duplicate": False,
    }
    defaults.update(overrides)
    a = Article(**defaults)
    db.add(a)
    db.flush()
    return a


class TestEmptyCandidates:
    def test_no_candidates_returns_empty_report(self, db):
        report = nextday_merge(db)
        assert isinstance(report, MergeReport)
        assert report.merged == []
        assert report.new_topics == []
        assert report.skipped_no_merge == []
        assert report.skipped_locked == []

    def test_no_nascent_topics_no_merge(self, db):
        # 只有 forming/confirmed 议题，无 nascent → 候选集为空
        _make_topic(db, name="confirmed 议题", lifecycle_state="confirmed",
                    centroid=_unit(0))
        report = nextday_merge(db)
        assert report.merged == []
        assert report.new_topics == []


class TestHighSimilarityMerge:
    def test_high_similarity_merge_success(self, db):
        """高相似度（≥0.85）候选归并：merged_into / topic_articles / revision_log / target 更新。"""
        now = datetime.now(UTC)
        source = make_source(db, country_code="US")
        # target：活跃 forming 议题，向量与 candidate 高相似
        target = _make_topic(
            db,
            name="目标议题",
            lifecycle_state="forming",
            centroid=_unit(0),
            country_scope=["US"],
            last_seen_at=now,
        )
        # candidate：孤立微簇（nascent，近 24h）
        cand = _make_topic(
            db,
            name="候选议题",
            lifecycle_state="nascent",
            centroid=_vec_with_cosine(0.90),
            country_scope=["CN"],
            first_seen_at=now - timedelta(hours=2),
            last_seen_at=now - timedelta(hours=2),
        )
        # candidate 两篇文章（归并后应迁往 target）
        a1 = _persist_article(db, source, embedding=_vec_with_cosine(0.92), country_code="CN")
        a2 = _persist_article(db, source, embedding=_vec_with_cosine(0.88), country_code="CN")
        db.add(TopicArticle(topic_id=cand.id, article_id=a1.id, weight=0.95, assign_method="online"))
        db.add(TopicArticle(topic_id=cand.id, article_id=a2.id, weight=0.85, assign_method="online"))
        db.commit()

        report = nextday_merge(db)
        db.commit()

        assert len(report.merged) == 1
        decision = report.merged[0]
        assert decision.source_topic_id == cand.id
        assert decision.target_topic_id == target.id
        assert decision.similarity >= 0.85

        db.expire_all()
        cand_db = db.get(Topic, cand.id)
        target_db = db.get(Topic, target.id)
        # 源议题：merged_into 设置 + lifecycle_state='evolving'
        assert cand_db.merged_into == target.id
        assert cand_db.lifecycle_state == "evolving"
        # 目标议题：国家并集
        assert set(target_db.country_scope) == {"US", "CN"}
        # 源议题文章全部迁往 target
        rows = db.query(TopicArticle).filter(TopicArticle.topic_id == target.id).all()
        assert {r.article_id for r in rows} == {a1.id, a2.id}
        assert all(r.assign_method == "merge" for r in rows)
        # 双方 revision_log 留痕
        src_fields = [e.get("field") for e in cand_db.revision_log]
        tgt_fields = [e.get("field") for e in target_db.revision_log]
        assert "merged_into" in src_fields
        assert "merged_from" in tgt_fields
        src_entry = next(e for e in cand_db.revision_log if e["field"] == "merged_into")
        assert src_entry["actor"] == "machine"
        assert src_entry["after_value"] == str(target.id)
        assert src_entry["trigger_evidence"]["algorithm"] == "nextday_merge"
        assert src_entry["trigger_evidence"]["similarity"] >= 0.85
        # target centroid 已更新（不再是 _unit(0)）
        assert target_db.centroid is not None


class TestLowSimilarityNoMerge:
    def test_below_threshold_keeps_new_topic(self, db):
        """低相似度（<0.85）：保留新 topic_id，归并不发生。"""
        now = datetime.now(UTC)
        target = _make_topic(
            db,
            name="目标议题",
            lifecycle_state="forming",
            centroid=_unit(0),
            last_seen_at=now,
        )
        cand = _make_topic(
            db,
            name="低相似候选",
            lifecycle_state="nascent",
            centroid=_vec_with_cosine(0.50),  # 远低于 0.85 阈值
            first_seen_at=now - timedelta(hours=1),
            last_seen_at=now - timedelta(hours=1),
        )
        db.commit()

        report = nextday_merge(db)
        db.commit()

        assert report.merged == []
        assert cand.id in report.new_topics
        db.expire_all()
        cand_db = db.get(Topic, cand.id)
        target_db = db.get(Topic, target.id)
        assert cand_db.merged_into is None
        assert cand_db.lifecycle_state == "nascent"
        # target 未被修改
        assert target_db.merged_into is None


class TestNoMergeListBlocks:
    def test_no_merge_with_blocks_high_similarity(self, db):
        """no_merge_with 阻止归并：高相似度但 (c, target) ∈ no_merge_pairs → 跳过。"""
        now = datetime.now(UTC)
        target = _make_topic(
            db,
            name="目标议题",
            lifecycle_state="forming",
            centroid=_unit(0),
            last_seen_at=now,
        )
        cand = _make_topic(
            db,
            name="被拉黑候选",
            lifecycle_state="nascent",
            centroid=_vec_with_cosine(0.92),  # 高相似度
            first_seen_at=now - timedelta(hours=1),
            last_seen_at=now - timedelta(hours=1),
        )
        # 写入 no_merge_with（误并回滚名单）
        cand.no_merge_with = [str(target.id)]
        db.commit()

        report = nextday_merge(db)
        db.commit()

        assert report.merged == []
        assert (cand.id, target.id) in report.skipped_no_merge
        assert cand.id in report.new_topics
        db.expire_all()
        cand_db = db.get(Topic, cand.id)
        assert cand_db.merged_into is None
        assert cand_db.lifecycle_state == "nascent"


class TestHumanLockedSkipped:
    def test_human_locked_merged_into_skipped(self, db):
        """human_locked_fields 含 'merged_into' 的源议题不自动归并（人工优先）。"""
        now = datetime.now(UTC)
        _make_topic(
            db,
            name="目标议题",
            lifecycle_state="forming",
            centroid=_unit(0),
            last_seen_at=now,
        )
        cand = _make_topic(
            db,
            name="人工锁定候选",
            lifecycle_state="nascent",
            centroid=_vec_with_cosine(0.92),
            first_seen_at=now - timedelta(hours=1),
            last_seen_at=now - timedelta(hours=1),
            human_locked_fields=["merged_into"],
        )
        db.commit()

        report = nextday_merge(db)
        db.commit()

        assert report.merged == []
        assert cand.id in report.skipped_locked
        db.expire_all()
        cand_db = db.get(Topic, cand.id)
        assert cand_db.merged_into is None


class TestArchivedNotInDossierPool:
    def test_archived_target_not_in_dossier(self, db):
        """archived 议题不在档案池 D：候选不会因为与 archived 高相似而归并到 archived。"""
        now = datetime.now(UTC)
        archived = _make_topic(
            db,
            name="归档议题",
            lifecycle_state="archived",
            centroid=_unit(0),
            last_seen_at=now,
        )
        cand = _make_topic(
            db,
            name="候选议题",
            lifecycle_state="nascent",
            centroid=_vec_with_cosine(0.95),
            first_seen_at=now - timedelta(hours=1),
            last_seen_at=now - timedelta(hours=1),
        )
        db.commit()

        report = nextday_merge(db)
        db.commit()

        # archived 不在档案池，候选找不到合法 target → 保留新 topic_id
        assert report.merged == []
        assert cand.id in report.new_topics
        db.expire_all()
        archived_db = db.get(Topic, archived.id)
        cand_db = db.get(Topic, cand.id)
        assert archived_db.lifecycle_state == "archived"
        assert cand_db.merged_into is None


class TestCandidateWindow:
    def test_old_nascent_not_in_candidate_pool(self, db):
        """candidate_since 之外的 nascent 议题不参与本轮归并。"""
        now = datetime.now(UTC)
        _make_topic(
            db,
            name="目标议题",
            lifecycle_state="forming",
            centroid=_unit(0),
            last_seen_at=now,
        )
        old_nascent = _make_topic(
            db,
            name="老 nascent",
            lifecycle_state="nascent",
            centroid=_vec_with_cosine(0.95),
            first_seen_at=now - timedelta(days=3),  # 超出默认 24h 候选窗口
            last_seen_at=now - timedelta(days=3),
        )
        db.commit()

        report = nextday_merge(db)
        db.commit()

        assert report.merged == []
        assert old_nascent.id not in report.new_topics  # 不在候选集，自然不在 new_topics


class _FakeRedis:
    """最小 Redis 替身：仅实现 smembers（filter_blacklisted 读取黑名单的唯一入口）。"""

    def __init__(self, members):
        self._members = set(members)

    def smembers(self, _key):
        return set(self._members)


class TestKeywordOverlapBlacklist:
    """T3.5 联动：归并比对的关键词重叠剔除黑名单实体后计算（门槛仍只看向量）。"""

    def _setup_merge(self, db):
        now = datetime.now(UTC)
        target = _make_topic(
            db,
            name="目标议题",
            lifecycle_state="forming",
            centroid=_unit(0),
            keywords=["United States", "天然气", "能源"],
            last_seen_at=now,
        )
        cand = _make_topic(
            db,
            name="候选议题",
            lifecycle_state="nascent",
            centroid=_vec_with_cosine(0.92),
            keywords=["United States", "天然气", "制裁"],
            first_seen_at=now - timedelta(hours=1),
            last_seen_at=now - timedelta(hours=1),
        )
        db.commit()
        return cand, target

    def test_blacklist_entity_excluded_from_overlap(self, db):
        """共享关键词中的黑名单实体（United States）被剔除，不参与重叠留痕。"""
        cand, target = self._setup_merge(db)
        fake_redis = _FakeRedis({"United States"})

        report = nextday_merge(db, redis_client=fake_redis)
        db.commit()

        assert len(report.merged) == 1
        overlap = report.merged[0].keyword_overlap
        assert overlap is not None
        assert overlap["blacklist_applied"] is True
        assert overlap["shared_keywords"] == ["天然气"]
        assert overlap["filtered_out"] == ["United States"]
        # 归并留痕同步写入 revision trigger_evidence
        db.expire_all()
        cand_db = db.get(Topic, cand.id)
        entry = next(e for e in cand_db.revision_log if e["field"] == "merged_into")
        assert entry["trigger_evidence"]["keyword_overlap"]["shared_keywords"] == ["天然气"]
        assert entry["trigger_evidence"]["keyword_overlap"]["blacklist_applied"] is True

    def test_no_redis_marks_blacklist_not_applied(self, db):
        """redis_client 缺位：黑名单不生效，按原始关键词算重叠并标记 blacklist_applied=False。"""
        cand, _target = self._setup_merge(db)

        report = nextday_merge(db, redis_client=None)
        db.commit()

        assert len(report.merged) == 1
        overlap = report.merged[0].keyword_overlap
        assert overlap is not None
        assert overlap["blacklist_applied"] is False
        assert overlap["shared_keywords"] == ["United States", "天然气"]
        assert overlap["filtered_out"] == []


class TestMergeTriggersReestimate:
    """T3.13：归并完成后对 target 议题触发增量重估（详细设计 4.2 算法 3 末段）。"""

    def test_merge_reestimates_target_event_origin(self, db):
        """target 已有事件（origin_at 偏晚），并入含更早文章的候选后：
        事件 origin_at 被修正到更早报道时间，revision_log 触发类型为 merge。"""
        from app.agenda_engine.revision import reestimate_origin  # noqa: F401 确认触发路径可 import
        from app.models.agenda import AgendaEvent

        now = datetime.now(UTC)
        src = make_source(db, country_code="CN")
        target = _make_topic(
            db, name="目标议题", lifecycle_state="forming",
            centroid=_unit(0), country_scope=["CN"], last_seen_at=now,
        )
        # target 自己的文章 T0（事件锚点据此建立）
        t_article = _persist_article(db, src, embedding=_unit(0), published_at=now - timedelta(hours=2))
        db.add(TopicArticle(topic_id=target.id, article_id=t_article.id, weight=1.0, assign_method="online"))
        event = AgendaEvent(
            topic_id=target.id, round_no=1, status="suspected", confidence="suspected",
            origin_type="media", origin_country_code="CN",
            origin_at=now - timedelta(hours=2), origin_confidence="medium",
            follower_sequence=[], revision_log=[], human_locked_fields=[],
        )
        db.add(event)

        cand = _make_topic(
            db, name="候选议题", lifecycle_state="nascent",
            centroid=_vec_with_cosine(0.92), country_scope=["CN"],
            first_seen_at=now - timedelta(hours=5), last_seen_at=now - timedelta(hours=5),
        )
        # 候选议题的文章比 target 事件锚点更早
        c_article = _persist_article(db, src, embedding=_vec_with_cosine(0.90), published_at=now - timedelta(hours=5))
        db.add(TopicArticle(topic_id=cand.id, article_id=c_article.id, weight=1.0, assign_method="online"))
        db.commit()

        report = nextday_merge(db)
        db.commit()

        assert len(report.merged) == 1
        db.refresh(event)
        # 归并后 target 含更早文章 → 重估修正 origin_at
        assert event.origin_at == now - timedelta(hours=5)
        assert event.status == "revised"
        entry = next(e for e in event.revision_log if e["field"] == "origin_at")
        assert entry["trigger_evidence"]["type"] == "merge"
        assert entry["actor"] == "machine"

    def test_merge_without_event_no_reestimate(self, db):
        """target 无 AgendaEvent：归并正常完成，重估为空操作不报错。"""
        now = datetime.now(UTC)
        _make_topic(
            db, name="目标议题", lifecycle_state="forming",
            centroid=_unit(0), last_seen_at=now,
        )
        cand = _make_topic(
            db, name="候选议题", lifecycle_state="nascent",
            centroid=_vec_with_cosine(0.92),
            first_seen_at=now - timedelta(hours=1), last_seen_at=now - timedelta(hours=1),
        )
        db.commit()

        report = nextday_merge(db)
        db.commit()
        assert len(report.merged) == 1
        assert report.merged[0].source_topic_id == cand.id
