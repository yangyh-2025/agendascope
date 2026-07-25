"""T3.13+T3.14+T3.15 集成测试：真实 PG + 真实 API（TestClient）+ 真实 LLM 注入位。

覆盖：
  1. 完整链路：建 event → 模拟新证据（更早报道入库）→ reestimate_origin →
     revision_log 完整（前后值/触发证据/model/prompt_version）+ status='revised'
  2. 人工确认 API：POST /agenda-events/{id}/confirm → 200 + status='confirmed' + audit_logs 留痕
  3. 人工否决 API：先 reestimate 产生机器修正 → POST revisions/{seq}/reject →
     回滚 + human_locked_fields 增加 + 再次 reestimate 不再推翻被锁定字段
  4. 权限校验：registered 不能 confirm/reject（403）；未认证（401）
  5. 错误码：404 / 422 正确返回
"""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.agenda_engine.revision import reestimate_origin
from app.models.agenda import AgendaEvent
from app.models.article import Article
from app.models.audit import AuditLog
from app.models.topic import Topic, TopicArticle
from tests.conftest import make_source

pytestmark = pytest.mark.integration

T0 = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)


def _make_topic(db, **kwargs) -> Topic:
    now = datetime.now(UTC)
    defaults = {
        "name": "revision 集成测试议题",
        "name_auto": "revision 集成测试议题",
        "naming_method": "ctfidf_fallback",
        "cluster_method": "agglomerative",
        "keywords": ["测试"],
        "country_scope": ["US"],
        "lifecycle_state": "forming",
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
        "id": uuid4(),
        "source_id": source.id,
        "url": f"https://example.com/{uuid4().hex}",
        "url_hash": uuid4().hex.ljust(64, "0")[:64],
        "title": "revision 集成测试",
        "language": "en",
        "published_at": T0,
        "country_code": source.country_code,
        "time_source": "feed",
        "is_duplicate": False,
    }
    defaults.update(overrides)
    a = Article(**defaults)
    db.add(a)
    db.flush()
    return a


def _link(db, topic: Topic, article: Article) -> None:
    db.add(TopicArticle(topic_id=topic.id, article_id=article.id, weight=1.0, assign_method="online"))
    db.flush()


def _make_event(db, topic: Topic, **overrides) -> AgendaEvent:
    defaults = {
        "topic_id": topic.id,
        "round_no": 1,
        "status": "watching",
        "confidence": "watching",
        "origin_type": "media",
        "origin_country_code": "GB",
        "origin_source_id": None,
        "origin_entity_id": None,
        "origin_at": T0,
        "origin_confidence": "medium",
        "follower_sequence": [],
        "stats_evidence": None,
        "detection_method": "llm",
        "revision_log": [],
        "human_locked_fields": [],
    }
    defaults.update(overrides)
    event = AgendaEvent(**defaults)
    db.add(event)
    db.flush()
    return event


def _build_revision_scenario(db):
    """构造：议题（GB 文章 T0） + event + US 文章 T0-26h（待触发增量重估）。"""
    topic = _make_topic(db)
    gb_source = make_source(db, name="GB Media", country_code="GB")
    gb_article = _persist_article(db, gb_source, published_at=T0, country_code="GB")
    _link(db, topic, gb_article)
    event = _make_event(db, topic, origin_country_code="GB", status="watching")
    db.commit()

    us_source = make_source(db, name="US Media", country_code="US")
    us_article = _persist_article(
        db, us_source, published_at=T0 - timedelta(hours=26), country_code="US",
    )
    _link(db, topic, us_article)
    db.commit()
    return topic, event, us_article


class TestReestimateFullChain:
    def test_full_chain_revision_log_complete(self, db):
        """完整链路：建 event → 新证据 → reestimate → revision_log 完整 + status='revised'。"""
        topic, event, us_article = _build_revision_scenario(db)

        result = reestimate_origin(
            db, topic.id,
            trigger={"type": "earlier_article", "article_id": str(us_article.id)},
        )
        db.commit()

        assert result is not None
        db.expire_all()
        event_db = db.get(AgendaEvent, event.id)

        # origin 已修正为 US
        assert event_db.origin_country_code == "US"
        # status='revised'
        assert event_db.status == "revised"

        # revision_log 完整字段（不变量①②③）
        for entry in event_db.revision_log:
            assert entry["before_value"] != entry["after_value"], "不变量①违反"
            assert isinstance(entry["trigger_evidence"], dict) and entry["trigger_evidence"], "不变量②违反"
            if entry["actor"] == "machine":
                assert entry["model"], "不变量③ model 缺失"
                assert entry["prompt_version"], "不变量③ prompt_version 缺失"
            assert entry["rejected"] is False

        # 含 origin_country_code 修正条目
        country_entries = [
            e for e in event_db.revision_log if e["field"] == "origin_country_code"
        ]
        assert len(country_entries) == 1
        country_entry = country_entries[0]
        assert country_entry["before_value"] == "GB"
        assert country_entry["after_value"] == "US"
        assert country_entry["actor"] == "machine"
        assert country_entry["trigger_evidence"]["type"] == "earlier_article"


class TestConfirmApi:
    def test_confirm_200_and_audit(self, client, db, admin_user, auth_headers):
        """POST /agenda-events/{id}/confirm 200 + status='confirmed' + audit_logs 留痕。"""
        topic = _make_topic(db)
        event = _make_event(db, topic, status="watching", confidence="watching")
        db.commit()

        resp = client.post(
            f"/api/v1/agenda-events/{event.id}/confirm",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["status"] == "confirmed"
        assert body["data"]["confidence"] == "confirmed"
        assert body["data"]["confirmed_by"] == str(admin_user.id)
        assert body["data"]["confirmed_at"] is not None

        # audit_logs 写入
        stmt = select(AuditLog).where(AuditLog.action == "agenda_event.confirm")
        entries = list(db.scalars(stmt).all())
        assert len(entries) >= 1
        latest = entries[-1]
        assert latest.result == "success"
        assert str(event.id) in (latest.resource or "")

    def test_confirm_401_unauthenticated(self, client, db):
        """未认证 → 401。"""
        resp = client.post(f"/api/v1/agenda-events/{uuid4()}/confirm")
        assert resp.status_code == 401

    def test_confirm_403_registered_role(self, client, db):
        """registered 角色 → 403。"""
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
            f"/api/v1/agenda-events/{uuid4()}/confirm",
            headers=headers,
        )
        assert resp.status_code == 403

    def test_confirm_404_event_not_found(self, client, db, auth_headers):
        """事件不存在 → 404 + code=3001。"""
        resp = client.post(
            f"/api/v1/agenda-events/{uuid4()}/confirm",
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == 3001

    def test_confirm_422_already_confirmed(self, client, db, admin_user, auth_headers):
        """已 confirmed 不可重复确认 → 422 + code=4002 + audit(failure)。"""
        topic = _make_topic(db)
        event = _make_event(db, topic, status="confirmed", confidence="confirmed")
        db.commit()

        resp = client.post(
            f"/api/v1/agenda-events/{event.id}/confirm",
            headers=auth_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == 4002

        # audit failure 留痕
        stmt = select(AuditLog).where(
            AuditLog.action == "agenda_event.confirm",
            AuditLog.result == "failure",
        )
        entries = list(db.scalars(stmt).all())
        assert len(entries) >= 1


class TestRejectApi:
    def test_reject_full_chain(self, client, db, admin_user, auth_headers):
        """完整否决链路：reestimate 机器修正 → API 否决 → 回滚 + 锁定 + 不再被推翻。"""
        topic, event, us_article = _build_revision_scenario(db)

        # 1) 触发机器修正
        reestimate_origin(
            db, topic.id,
            trigger={"type": "earlier_article", "article_id": str(us_article.id)},
        )
        db.commit()

        db.expire_all()
        event_db = db.get(AgendaEvent, event.id)
        assert event_db.origin_country_code == "US"
        # 找机器修正 seq
        machine_entry = next(
            e for e in event_db.revision_log
            if e["field"] == "origin_country_code" and e["actor"] == "machine"
        )
        machine_seq = machine_entry["seq"]

        # 2) API 否决
        resp = client.post(
            f"/api/v1/agenda-events/{event.id}/revisions/{machine_seq}/reject",
            json={"reason": "更早报道实为转载组误判，维持原首发判定"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["origin_country_code"] == "GB"  # 已回滚
        assert "origin_country_code" in body["data"]["human_locked_fields"]
        # revision_appended 是人工追加的回滚条目
        appended = body["data"]["revision_appended"]
        assert appended["actor"] == "human"
        assert appended["field"] == "origin_country_code"
        assert appended["before_value"] == "US"
        assert appended["after_value"] == "GB"

        # audit_logs 写入
        stmt = select(AuditLog).where(AuditLog.action == "agenda_event.revision_reject")
        entries = list(db.scalars(stmt).all())
        assert len(entries) >= 1
        latest = entries[-1]
        assert latest.result == "success"

        # 3) 再次 reestimate：被锁定字段不再被机器推翻
        reestimate_origin(
            db, topic.id,
            trigger={"type": "earlier_article", "article_id": str(uuid4())},
        )
        db.commit()
        db.expire_all()
        event_db2 = db.get(AgendaEvent, event.id)
        assert event_db2.origin_country_code == "GB"  # 机器未推翻人工

    def test_reject_404_event_not_found(self, client, db, auth_headers):
        """事件不存在 → 404。"""
        resp = client.post(
            f"/api/v1/agenda-events/{uuid4()}/revisions/1/reject",
            json={"reason": "测试"},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == 3001

    def test_reject_404_seq_not_found(self, client, db, auth_headers):
        """revision_seq 不存在 → 404。"""
        topic = _make_topic(db)
        event = _make_event(db, topic)
        db.commit()

        resp = client.post(
            f"/api/v1/agenda-events/{event.id}/revisions/999/reject",
            json={"reason": "测试"},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == 3001

    def test_reject_422_already_rejected(self, client, db, admin_user, auth_headers):
        """同一 revision 已被否决：再次否决 → 422。"""
        topic, event, us_article = _build_revision_scenario(db)
        reestimate_origin(
            db, topic.id,
            trigger={"type": "earlier_article", "article_id": str(us_article.id)},
        )
        db.commit()

        db.expire_all()
        event_db = db.get(AgendaEvent, event.id)
        machine_entry = next(
            e for e in event_db.revision_log
            if e["field"] == "origin_country_code" and e["actor"] == "machine"
        )
        machine_seq = machine_entry["seq"]

        # 第一次否决（成功）
        resp1 = client.post(
            f"/api/v1/agenda-events/{event.id}/revisions/{machine_seq}/reject",
            json={"reason": "首次否决"},
            headers=auth_headers,
        )
        assert resp1.status_code == 200

        # 第二次否决（应 422）
        resp2 = client.post(
            f"/api/v1/agenda-events/{event.id}/revisions/{machine_seq}/reject",
            json={"reason": "再次否决"},
            headers=auth_headers,
        )
        assert resp2.status_code == 422
        assert resp2.json()["code"] == 4002


class TestListRevisionsApi:
    def test_list_revisions_200(self, client, db, admin_user, auth_headers):
        """GET /agenda-events/{id}/revisions 返回完整 revision_log。"""
        topic, event, us_article = _build_revision_scenario(db)
        reestimate_origin(
            db, topic.id,
            trigger={"type": "earlier_article", "article_id": str(us_article.id)},
        )
        db.commit()

        resp = client.get(
            f"/api/v1/agenda-events/{event.id}/revisions",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        assert data["event_id"] == str(event.id)
        assert data["topic_id"] == str(topic.id)
        assert data["status"] == "revised"
        assert isinstance(data["revisions"], list)
        assert len(data["revisions"]) >= 1
        # 每条 revision 字段完整
        for entry in data["revisions"]:
            assert "seq" in entry
            assert "field" in entry
            assert "before_value" in entry
            assert "after_value" in entry
            assert "actor" in entry
            assert "rejected" in entry

    def test_list_revisions_registered_allowed(self, client, db):
        """registered 角色可读 revisions。"""
        from app.core.security import hash_password
        from app.models.user import User

        reg_user = User(
            username="reg.reader",
            password_hash=hash_password("User12345A"),
            display_name="reg.reader",
            role="registered",
            must_change_password=False,
        )
        db.add(reg_user)
        topic = _make_topic(db)
        event = _make_event(db, topic)
        db.commit()

        login = client.post(
            "/api/v1/auth/login",
            json={"username": "reg.reader", "password": "User12345A"},
        )
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
        resp = client.get(
            f"/api/v1/agenda-events/{event.id}/revisions",
            headers=headers,
        )
        assert resp.status_code == 200

    def test_list_revisions_404_event_not_found(self, client, db, auth_headers):
        """事件不存在 → 404。"""
        resp = client.get(
            f"/api/v1/agenda-events/{uuid4()}/revisions",
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == 3001
