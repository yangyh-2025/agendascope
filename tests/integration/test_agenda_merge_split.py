"""T3.3+T3.4 归并与分裂集成测试：真实 PG + 真实 API（TestClient）。

覆盖：
  1. 完整链路：candidate A（2 文章）→ nextday_merge 归并入 target B → split_topic 拆分 →
     A 恢复独立 topic_id + 文章回归 + no_merge_with 双向写入 + revision_log 完整
  2. 分裂 422：child 不是 parent 归并而来
  3. 分裂后再次归并被 no_merge_with 阻止（验证"不可归并"名单生效）
  4. API：POST /api/v1/topics/{parent_id}/split 走 client + auth_headers fixture
     验证 200/404/422 响应结构 + audit_logs 写入
"""
import math
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.agenda_engine.merge import nextday_merge
from app.agenda_engine.split import SplitError, split_topic
from app.models.article import Article
from app.models.audit import AuditLog
from app.models.topic import Topic, TopicArticle
from tests.conftest import make_source

pytestmark = pytest.mark.integration

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
    defaults = {
        "id": uuid.uuid4(),
        "source_id": source.id,
        "url": f"https://example.com/{uuid.uuid4().hex}",
        "url_hash": uuid.uuid4().hex.ljust(64, "0")[:64],
        "title": "integration test article",
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


def _build_merge_scenario(db):
    """构造 candidate A（2 文章）+ target B，A 与 B 高相似（≥0.85）。

    target 是更早创建的既有议题（first_seen 2 天前）：归并方向为"新并入老"，
    更老 topic_id 存续（算法 3"topic_id 复用"口径，target 须不晚于 candidate）。
    """
    now = datetime.now(UTC)
    source = make_source(db, country_code="US")
    target = _make_topic(
        db,
        name="目标议题 B",
        lifecycle_state="forming",
        centroid=_unit(0),
        country_scope=["US"],
        first_seen_at=now - timedelta(days=2),
        last_seen_at=now,
    )
    cand = _make_topic(
        db,
        name="候选议题 A",
        lifecycle_state="nascent",
        centroid=_vec_with_cosine(0.92),
        country_scope=["CN"],
        first_seen_at=now - timedelta(hours=2),
        last_seen_at=now - timedelta(hours=2),
    )
    a1 = _persist_article(db, source, embedding=_vec_with_cosine(0.93), country_code="CN")
    a2 = _persist_article(db, source, embedding=_vec_with_cosine(0.91), country_code="CN")
    db.add(TopicArticle(topic_id=cand.id, article_id=a1.id, weight=0.95, assign_method="online"))
    db.add(TopicArticle(topic_id=cand.id, article_id=a2.id, weight=0.85, assign_method="online"))
    db.commit()
    return cand, target, [a1, a2]


class TestMergeThenSplitFullChain:
    def test_merge_then_split_restores_state(self, db, admin_user):
        """完整链路：归并 → 分裂 → 全部恢复 + 双向 no_merge_with + revision_log 完整。"""
        cand, target, articles = _build_merge_scenario(db)
        cand_id, target_id = cand.id, target.id
        article_ids = {a.id for a in articles}

        # 1) 归并
        report = nextday_merge(db)
        db.commit()
        assert len(report.merged) == 1
        assert report.merged[0].source_topic_id == cand_id
        assert report.merged[0].target_topic_id == target_id

        # 归并后：文章在 target
        rows = db.query(TopicArticle).filter(TopicArticle.topic_id == target_id).all()
        assert {r.article_id for r in rows} == article_ids
        assert all(r.assign_method == "merge" for r in rows)

        # 2) 分裂
        parent, child = split_topic(db, target_id, cand_id, actor_user_id=admin_user.id)
        db.commit()

        # 3) 验证 child 恢复独立 topic_id
        assert child.id == cand_id
        assert child.merged_into is None
        # 4) 文章回归 child
        child_rows = db.query(TopicArticle).filter(TopicArticle.topic_id == cand_id).all()
        assert {r.article_id for r in child_rows} == article_ids
        # assign_method 改回 'online'
        assert all(r.assign_method == "online" for r in child_rows)
        # weight 保留
        weights = sorted(float(r.weight) for r in child_rows)
        assert weights == sorted([0.95, 0.85])

        # 5) 双向 no_merge_with 写入
        assert str(cand_id) in (parent.no_merge_with or [])
        assert str(target_id) in (child.no_merge_with or [])

        # 6) revision_log 完整：merged_into + split_from
        parent_fields = [e.get("field") for e in parent.revision_log]
        child_fields = [e.get("field") for e in child.revision_log]
        assert "merged_from" in parent_fields  # 归并时写入
        assert "split_from" in parent_fields   # 分裂时写入
        assert "merged_into" in child_fields
        assert "split_from" in child_fields
        # 分裂的 revision_log actor='human' trigger='manual_split'
        split_entry_parent = next(e for e in parent.revision_log if e["field"] == "split_from")
        split_entry_child = next(e for e in child.revision_log if e["field"] == "split_from")
        assert split_entry_parent["actor"] == "human"
        assert split_entry_parent["trigger"] == "manual_split"
        assert split_entry_parent["actor_id"] == str(admin_user.id)
        assert split_entry_child["actor"] == "human"
        assert split_entry_child["after_value"] == str(target_id)
        assert split_entry_parent["after_value"] == str(cand_id)

        # 7) parent 文章已迁出（不再有 merge 归属）
        parent_rows = db.query(TopicArticle).filter(TopicArticle.topic_id == target_id).all()
        assert all(r.assign_method != "merge" for r in parent_rows)


class TestSplitValidation:
    def test_split_422_child_not_merged_from_parent(self, db, admin_user):
        """分裂 422：child.merged_into != parent_id（child 不是 parent 归并而来）。"""
        now = datetime.now(UTC)
        parent = _make_topic(
            db, name="parent", lifecycle_state="forming",
            centroid=_unit(0), last_seen_at=now,
        )
        other_target = _make_topic(
            db, name="other target", lifecycle_state="forming",
            centroid=_unit(2), last_seen_at=now,
        )
        # child 归并到别的议题，而非 parent
        child = _make_topic(
            db, name="child", lifecycle_state="evolving",
            centroid=_unit(3), last_seen_at=now,
            merged_into=other_target.id,
        )
        db.commit()

        with pytest.raises(SplitError) as exc_info:
            split_topic(db, parent.id, child.id, actor_user_id=admin_user.id)
        assert exc_info.value.code == 4002

    def test_split_422_parent_archived(self, db, admin_user):
        """分裂 422：parent 已 archived。"""
        now = datetime.now(UTC)
        parent = _make_topic(
            db, name="parent archived", lifecycle_state="archived",
            centroid=_unit(0), last_seen_at=now,
        )
        child = _make_topic(
            db, name="child", lifecycle_state="evolving",
            centroid=_unit(1), last_seen_at=now,
            merged_into=parent.id,
        )
        db.commit()

        with pytest.raises(SplitError) as exc_info:
            split_topic(db, parent.id, child.id, actor_user_id=admin_user.id)
        assert exc_info.value.code == 4002

    def test_split_404_parent_not_found(self, db, admin_user):
        """分裂 404：parent 不存在。"""
        now = datetime.now(UTC)
        child = _make_topic(
            db, name="child", lifecycle_state="evolving",
            centroid=_unit(0), last_seen_at=now,
        )
        db.commit()

        with pytest.raises(SplitError) as exc_info:
            split_topic(db, uuid.uuid4(), child.id, actor_user_id=admin_user.id)
        assert exc_info.value.code == 3001

    def test_split_404_child_not_found(self, db, admin_user):
        """分裂 404：child 不存在。"""
        now = datetime.now(UTC)
        parent = _make_topic(
            db, name="parent", lifecycle_state="forming",
            centroid=_unit(0), last_seen_at=now,
        )
        db.commit()

        with pytest.raises(SplitError) as exc_info:
            split_topic(db, parent.id, uuid.uuid4(), actor_user_id=admin_user.id)
        assert exc_info.value.code == 3001


class TestNoMergeListPreventsReMerge:
    def test_split_blocks_subsequent_merge(self, db, admin_user):
        """分裂后再次归并被 no_merge_with 阻止（验证"不可归并"名单生效）。"""
        cand, target, _ = _build_merge_scenario(db)
        cand_id, target_id = cand.id, target.id

        # 1) 归并
        report1 = nextday_merge(db)
        db.commit()
        assert len(report1.merged) == 1

        # 2) 分裂
        split_topic(db, target_id, cand_id, actor_user_id=admin_user.id)
        db.commit()

        # 3) 把 cand 改回 nascent + 近 24h（让它重新进入候选池）
        cand_db = db.get(Topic, cand_id)
        cand_db.lifecycle_state = "nascent"
        cand_db.first_seen_at = datetime.now(UTC)
        db.commit()

        # 4) 再次归并：cand 与 target 仍然高相似，但 no_merge_with 阻止
        report2 = nextday_merge(db)
        db.commit()

        # 不应再发生归并
        merged_pairs = {(d.source_topic_id, d.target_topic_id) for d in report2.merged}
        assert (cand_id, target_id) not in merged_pairs
        # 应记录在 skipped_no_merge
        assert (cand_id, target_id) in report2.skipped_no_merge


class TestSplitApi:
    def test_api_split_200(self, client, db, admin_user, auth_headers):
        """API：POST /topics/{parent_id}/split 200 响应结构。"""
        cand, target, _ = _build_merge_scenario(db)
        cand_id, target_id = cand.id, target.id

        # 1) 先触发归并
        report = nextday_merge(db)
        db.commit()
        assert len(report.merged) == 1

        # 2) API 分裂
        resp = client.post(
            f"/api/v1/topics/{target_id}/split",
            json={"child_topic_id": str(cand_id)},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["parent_id"] == str(target_id)
        assert body["data"]["child_id"] == str(cand_id)
        assert body["data"]["restored_topic_id"] == str(cand_id)
        assert set(body["data"]["no_merge_pair"]) == {str(target_id), str(cand_id)}

        # 3) audit_logs 写入
        stmt = select(AuditLog).where(AuditLog.action == "topic.split")
        entries = list(db.scalars(stmt).all())
        assert len(entries) >= 1
        latest = entries[-1]
        assert latest.result == "success"
        assert str(target_id) in (latest.resource or "")

    def test_api_split_404_parent_not_found(self, client, db, auth_headers):
        """API：parent 不存在 → 404。"""
        resp = client.post(
            f"/api/v1/topics/{uuid.uuid4()}/split",
            json={"child_topic_id": str(uuid.uuid4())},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == 3001

    def test_api_split_422_child_not_from_parent(self, client, db, auth_headers):
        """API：child 不是 parent 归并而来 → 422。"""
        now = datetime.now(UTC)
        parent = _make_topic(
            db, name="parent", lifecycle_state="forming",
            centroid=_unit(0), last_seen_at=now,
        )
        other = _make_topic(
            db, name="other", lifecycle_state="forming",
            centroid=_unit(1), last_seen_at=now,
        )
        child = _make_topic(
            db, name="child", lifecycle_state="evolving",
            centroid=_unit(2), last_seen_at=now,
            merged_into=other.id,
        )
        db.commit()

        resp = client.post(
            f"/api/v1/topics/{parent.id}/split",
            json={"child_topic_id": str(child.id)},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == 4002

    def test_api_split_401_unauthenticated(self, client, db):
        """API：未认证 → 401。"""
        resp = client.post(
            f"/api/v1/topics/{uuid.uuid4()}/split",
            json={"child_topic_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 401

    def test_api_split_403_registered_role(self, client, db):
        """API：registered 角色 → 403（需 authorized）。"""
        from app.core.security import hash_password
        from app.models.user import User

        reg_user = User(
            username="reg.user",
            password_hash=hash_password("User12345A"),
            display_name="reg.user",
            role="registered",
            must_change_password=False,
        )
        db.add(reg_user)
        db.commit()

        login = client.post(
            "/api/v1/auth/login",
            json={"username": "reg.user", "password": "User12345A"},
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
        resp = client.post(
            f"/api/v1/topics/{uuid.uuid4()}/split",
            json={"child_topic_id": str(uuid.uuid4())},
            headers=headers,
        )
        assert resp.status_code == 403

    def test_api_split_audit_failure_written(self, client, db, auth_headers):
        """API：分裂校验失败也写 audit_logs(result=failure)。"""
        now = datetime.now(UTC)
        parent = _make_topic(
            db, name="parent", lifecycle_state="forming",
            centroid=_unit(0), last_seen_at=now,
        )
        other = _make_topic(
            db, name="other", lifecycle_state="forming",
            centroid=_unit(1), last_seen_at=now,
        )
        child = _make_topic(
            db, name="child", lifecycle_state="evolving",
            centroid=_unit(2), last_seen_at=now,
            merged_into=other.id,
        )
        db.commit()

        resp = client.post(
            f"/api/v1/topics/{parent.id}/split",
            json={"child_topic_id": str(child.id)},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        stmt = select(AuditLog).where(AuditLog.action == "topic.split")
        entries = [e for e in db.scalars(stmt).all() if e.result == "failure"]
        assert len(entries) >= 1


class TestNextdayMergeHistoricalTimeline:
    """M5 回放"次日归并 0%"根因回归：议题时间戳在历史日期时，
    活跃窗口必须可注入时间基准（now），否则墙钟窗口把历史议题全部排除。"""

    def _historical_scenario(self, db):
        hist = datetime(2021, 3, 25, 0, tzinfo=UTC)
        source = make_source(db, country_code="US")
        target = _make_topic(
            db, name="历史目标议题", lifecycle_state="forming",
            centroid=_unit(0), country_scope=["US"],
            first_seen_at=hist - timedelta(days=1), last_seen_at=hist - timedelta(days=1),
        )
        cand = _make_topic(
            db, name="历史候选议题", lifecycle_state="nascent",
            centroid=_vec_with_cosine(0.92), country_scope=["CN"],
            first_seen_at=hist - timedelta(hours=3), last_seen_at=hist - timedelta(hours=3),
        )
        a1 = _persist_article(db, source, embedding=_vec_with_cosine(0.93), country_code="CN")
        db.add(TopicArticle(topic_id=cand.id, article_id=a1.id, weight=0.95, assign_method="online"))
        db.commit()
        return hist, cand, target

    def test_wall_clock_window_excludes_historical_topics(self, db):
        """对照：缺省墙钟基准 → 历史议题落在活跃窗口外，找不到归并目标。"""
        hist, cand, _target = self._historical_scenario(db)
        report = nextday_merge(db, candidate_since=hist - timedelta(days=2))
        db.commit()
        assert len(report.merged) == 0
        assert cand.id in report.new_topics

    def test_merge_with_injected_now(self, db):
        """注入回放时间基准 → 历史议题在自身时间轴内正常归并。"""
        hist, cand, target = self._historical_scenario(db)
        report = nextday_merge(db, candidate_since=hist - timedelta(days=2), now=hist)
        db.commit()
        assert len(report.merged) == 1
        assert report.merged[0].source_topic_id == cand.id
        assert report.merged[0].target_topic_id == target.id

    def test_forming_candidate_merges_into_older_topic(self, db):
        """候选集 C 含 forming（算法 3"新议题/微簇集"口径）：同事件 forming 子簇
        不再因已获同伴而被排除在归并候选外（M5 回放误拆根因之二）。"""
        now = datetime.now(UTC)
        source = make_source(db, country_code="US")
        target = _make_topic(
            db, name="档案老议题", lifecycle_state="forming",
            centroid=_unit(0), country_scope=["US"],
            first_seen_at=now - timedelta(days=2), last_seen_at=now - timedelta(hours=2),
        )
        cand = _make_topic(
            db, name="forming 子簇", lifecycle_state="forming",
            centroid=_vec_with_cosine(0.92), country_scope=["CN"],
            first_seen_at=now - timedelta(hours=3), last_seen_at=now - timedelta(hours=3),
        )
        a1 = _persist_article(db, source, embedding=_vec_with_cosine(0.93), country_code="CN")
        a2 = _persist_article(db, source, embedding=_vec_with_cosine(0.91), country_code="CN")
        db.add(TopicArticle(topic_id=cand.id, article_id=a1.id, weight=0.95, assign_method="online"))
        db.add(TopicArticle(topic_id=cand.id, article_id=a2.id, weight=0.85, assign_method="online"))
        db.commit()

        report = nextday_merge(db)
        db.commit()
        assert len(report.merged) == 1
        assert report.merged[0].source_topic_id == cand.id
        assert report.merged[0].target_topic_id == target.id

    def test_older_topic_id_survives_when_both_new(self, db):
        """同窗两个新议题同事件：年轻议题并入更老议题——更老 topic_id 存续
        （算法 3"topic_id 复用"意图：target 须不晚于 candidate 创建）。"""
        now = datetime.now(UTC)
        older = _make_topic(
            db, name="较老新议题", lifecycle_state="forming",
            centroid=_unit(0), country_scope=["US"],
            first_seen_at=now - timedelta(hours=5), last_seen_at=now - timedelta(hours=5),
        )
        younger = _make_topic(
            db, name="较新子簇", lifecycle_state="forming",
            centroid=_vec_with_cosine(0.92), country_scope=["CN"],
            first_seen_at=now - timedelta(hours=2), last_seen_at=now - timedelta(hours=2),
        )
        db.commit()

        report = nextday_merge(db)
        db.commit()
        assert len(report.merged) == 1
        assert report.merged[0].source_topic_id == younger.id
        assert report.merged[0].target_topic_id == older.id

    def test_fixpoint_rereads_below_threshold_candidates(self, db):
        """单轮不动点迭代：候选 A 先评估略低于阈值（0.84<0.85），同轮候选 X 并入
        推高 target 质心后，A 在下一轮追平归并（消除单遍顺序伪影——M5 回放
        russia-ukraine 案例 a2 误拆根因：同轮先评估 0.608 未并，主簇同轮合并
        4 篇后 sim 升至 0.629 却无下一轮可追）。

        几何构造：T=e0；A=0.84·e0+0.5426·e1（sim(A,T)=0.84 低于阈值）；
        X=0.88·e0+0.2227·e1+0.4187·e2（sim(X,T)=0.88≥阈值，sim(X,A)=0.86<0.88
        保证 X 选 T 而非 A）；X 并入后 dt=12h 池化 cos(A,T')≈0.858≥0.85。
        """
        def _vec3(c0, c1, c2):
            v = [0.0] * DIM
            v[0], v[1], v[2] = c0, c1, c2
            return v

        now = datetime.now(UTC)
        target = _make_topic(
            db, name="主议题", lifecycle_state="forming",
            centroid=_unit(0), country_scope=["US"],
            first_seen_at=now - timedelta(days=2), last_seen_at=now - timedelta(hours=12),
        )
        cand_a = _make_topic(
            db, name="候选A-先评估低于阈值", lifecycle_state="nascent",
            centroid=_vec_with_cosine(0.84, 0, 1), country_scope=["CN"],
            first_seen_at=now - timedelta(hours=3), last_seen_at=now - timedelta(hours=3),
        )
        cand_x = _make_topic(
            db, name="候选X-推高质心", lifecycle_state="nascent",
            centroid=_vec3(0.88, 0.2227, 0.4187), country_scope=["GB"],
            first_seen_at=now - timedelta(hours=2), last_seen_at=now - timedelta(hours=2),
        )
        db.commit()

        report = nextday_merge(db)
        db.commit()
        merged_pairs = {(d.source_topic_id, d.target_topic_id) for d in report.merged}
        assert (cand_x.id, target.id) in merged_pairs
        assert (cand_a.id, target.id) in merged_pairs  # 不动点第二轮追平
