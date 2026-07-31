"""Phase 4 DB 相关集成测试：articles 计数 / map 聚合 / 链路 API / alerts / 订阅轮 / 报告生成。

需要本地 PostgreSQL（docker compose up -d db）；不可达时自动跳过。
注意：本文件直接调用路由函数（新路由尚未挂接 router.py，由集成方接线后可改走 TestClient）。
"""
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.agenda import AgendaEvent
from app.models.alert import Alert, AlertRule
from app.models.article import Article
from app.models.subscription import Subscription
from app.models.topic import AgendaSnapshot, Topic
from app.models.user import User

NOW = datetime.now(UTC)


def _user(db, role="authorized", email="u@example.com"):
    from app.core.security import hash_password
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        password_hash=hash_password("Passw0rd123"),
        display_name="测试用户", role=role, email=email,
    )
    db.add(user)
    db.flush()
    return user


def _topic(db, **kw):
    topic = Topic(
        name=kw.get("name", "Test Topic"), name_auto=kw.get("name", "Test Topic"),
        name_zh=kw.get("name_zh", "测试议题"), topic_category=kw.get("topic_category", "经济"),
        keywords=kw.get("keywords", ["测试"]), summary_zh="测试摘要",
    )
    db.add(topic)
    db.flush()
    return topic


def _snapshot(db, topic, cc, rank=1, count=10, neg=0.3):
    snap = AgendaSnapshot(
        country_code=cc, topic_id=topic.id,
        window_start=NOW - timedelta(hours=1), window_end=NOW,
        granularity="hour", article_count=count, salience_score=0.9,
        salience_rank=rank, sentiment_neg=neg, sentiment_pos=0.5,
    )
    db.add(snap)
    db.flush()
    return snap


def _article(db, source, cc="US", title="Hello", content="x" * 300):
    a = Article(
        source_id=source.id, url=f"https://example.com/{uuid.uuid4().hex}",
        url_hash=uuid.uuid4().hex * 2, title=title, content=content,
        language="en", published_at=NOW, visible_at=NOW, country_code=cc,
    )
    db.add(a)
    db.flush()
    return a


# ---------------------------------------------------------------------------
# articles total 计数（#8 回归）
# ---------------------------------------------------------------------------


class TestArticlesTotal:
    def test_total_respects_country_filter(self, db):
        from app.api.routes.articles import list_articles
        from tests.conftest import make_source

        src_us = make_source(db, country_code="US")
        src_jp = make_source(db, country_code="JP", feed_url=f"https://example.com/f{uuid.uuid4().hex}.xml")
        for _ in range(3):
            _article(db, src_us, cc="US")
        for _ in range(2):
            _article(db, src_jp, cc="JP")

        user = SimpleNamespace(role="authorized", id=uuid.uuid4())
        resp = list_articles(
            request=None, q=None, country_code="US", topic_id=None, language=None,
            date_from=None, date_to=None, page=1, page_size=20, db=db, es=None, user=user,
        )
        assert resp["data"]["total"] == 3
        assert len(resp["data"]["items"]) == 3

    def test_excerpt_truncated_to_150(self, db):
        from app.api.routes.articles import list_articles
        from tests.conftest import make_source

        src = make_source(db)
        _article(db, src, content="y" * 500)
        user = SimpleNamespace(role="authorized", id=uuid.uuid4())
        resp = list_articles(
            request=None, q=None, country_code=None, topic_id=None, language=None,
            date_from=None, date_to=None, page=1, page_size=20, db=db, es=None, user=user,
        )
        assert len(resp["data"]["items"][0]["excerpt"]) <= 150


# ---------------------------------------------------------------------------
# map 聚合（#9 回归）
# ---------------------------------------------------------------------------


class TestMapCountries:
    def test_all_30_countries_present_and_empty_marked(self, db):
        from app.api.routes.map import map_countries
        from tests.conftest import make_source

        src = make_source(db, country_code="US")
        _article(db, src, cc="US")
        topic = _topic(db)
        _snapshot(db, topic, "US")

        user = SimpleNamespace(role="registered", id=uuid.uuid4())
        today = NOW.strftime("%Y-%m-%d")
        resp = map_countries(date=today, db=db, user=user)
        items = resp["data"]["items"]
        codes = {i["country_code"] for i in items}
        assert len(codes) >= 30  # 30 国目标清单全下发
        us = next(i for i in items if i["country_code"] == "US")
        assert us["empty"] is False and us["article_count_today"] >= 1
        jp = next(i for i in items if i["country_code"] == "JP")
        assert jp["empty"] is True and jp["top_topics"] == []
        # 覆盖率以 30 国为分母：仅 1 国有数据 → ≈ 1/30
        assert resp["data"]["coverage_confidence"] == pytest.approx(1 / 30, abs=0.01)


# ---------------------------------------------------------------------------
# 传播链路 API（#7）
# ---------------------------------------------------------------------------


class TestEventChain:
    def test_chain_from_engine_fields(self, db):
        from app.api.routes.agenda_events import event_chain
        from tests.conftest import make_source

        src = make_source(db, country_code="US", name="Origin Media")
        topic = _topic(db)
        event = AgendaEvent(
            topic_id=topic.id, status="confirmed", confidence="confirmed",
            origin_type="media", origin_country_code="US", origin_source_id=src.id,
            origin_at=NOW - timedelta(hours=10), origin_confidence="high",
            follower_sequence=[
                {"country_code": "JP", "first_media_id": str(uuid.uuid4()),
                 "first_media_name": "JP Media", "first_article_id": str(uuid.uuid4()),
                 "first_published_at": (NOW - timedelta(hours=4)).isoformat(), "lag_hours": 6.0},
            ],
        )
        db.add(event)
        db.flush()

        user = SimpleNamespace(role="authorized", id=uuid.uuid4())
        resp = event_chain(event_id=event.id, db=db, user=user)
        data = resp["data"]
        assert data["origin"]["country"] == "US"
        assert data["origin"]["media"]["name"] == "Origin Media"
        assert data["origin"]["confidence"] == "high"
        assert data["follower_sequence"][0]["country"] == "JP"
        assert data["follower_sequence"][0]["lag_hours"] == 6.0
        assert data["edges"] == [{"from_country": "US", "to_country": "JP", "lag_hours": 6.0}]


# ---------------------------------------------------------------------------
# alerts 站内信（#4）
# ---------------------------------------------------------------------------


class TestAlertsApi:
    def _alert(self, db, user, status="unread"):
        rule = AlertRule(
            user_id=user.id, name="r1", country_codes=["US"], keywords=["k"],
            condition_type="growth_rate", condition_value=100,
        )
        db.add(rule)
        db.flush()
        alert = Alert(rule_id=rule.id, user_id=user.id, payload={"kind": "rule_triggered"}, status=status)
        db.add(alert)
        db.flush()
        return alert

    def test_list_filter_and_read(self, db):
        from app.api.routes.alerts import list_alerts, read_alert

        user = _user(db)
        a1 = self._alert(db, user)
        self._alert(db, user, status="read")

        resp = list_alerts(status="unread", page=1, page_size=20, db=db, user=user)
        assert resp["data"]["total"] == 1 and resp["data"]["unread"] == 1

        resp = read_alert(alert_id=a1.id, db=db, user=user)
        assert resp["data"]["status"] == "read"
        assert a1.read_at is not None

        resp = list_alerts(status=None, page=1, page_size=20, db=db, user=user)
        assert resp["data"]["unread"] == 0

    def test_read_all(self, db):
        from app.api.routes.alerts import read_all_alerts

        user = _user(db)
        self._alert(db, user)
        self._alert(db, user)
        resp = read_all_alerts(body=None, db=db, user=user)
        assert resp["data"]["marked"] == 2

    def test_cannot_read_others_alert(self, db):
        from app.api.routes.alerts import read_alert
        from app.core.errors import BizError

        owner = _user(db)
        other = _user(db)
        alert = self._alert(db, owner)
        with pytest.raises(BizError):
            read_alert(alert_id=alert.id, db=db, user=other)


# ---------------------------------------------------------------------------
# 订阅轮（#5）：无 SMTP → 失败终态 → 日终报告写 admin 站内信
# ---------------------------------------------------------------------------


class TestSubscriptionRound:
    def test_round_without_smtp_marks_failed_and_reports_admin(self, db):
        from app.alerting.subscription import run_subscription_round

        user = _user(db)
        sub = Subscription(user_id=user.id, country_codes=["US"], frequency="daily")
        db.add(sub)
        db.flush()
        topic = _topic(db)
        _snapshot(db, topic, "US")

        stats = run_subscription_round(db, smtp_config=None, now=NOW)
        assert stats["generated"] == 1 and stats["sent"] == 0
        assert stats["failed_final"] == 1  # 无 SMTP 直接终态 + 日终报告

        admin_alerts = db.query(Alert).filter(
            Alert.payload["kind"].astext == "subscription_delivery_failure_report"
        ).all()
        assert len(admin_alerts) == 1

        # 第二轮：不重复生成、不重复上报（reported 标记）
        stats2 = run_subscription_round(db, smtp_config=None, now=NOW)
        assert stats2["generated"] == 0 and stats2["failed_final"] == 0

    def test_unsubscribe_token_flow(self, db):
        from app.api.routes.subscriptions import unsubscribe

        user = _user(db)
        sub = Subscription(user_id=user.id, country_codes=["US"], frequency="weekly")
        db.add(sub)
        db.flush()
        resp = unsubscribe(token=sub.unsubscribe_token, db=db)
        assert resp["data"]["enabled"] is False
        assert sub.enabled is False


# ---------------------------------------------------------------------------
# 报告生成（#6）：真实生成 PDF/DOCX 文件 + pending 队列
# ---------------------------------------------------------------------------


class TestReportGeneration:
    def test_generate_topic_deep_pdf(self, db, tmp_path):
        from app.models.topic import TopicArticle
        from app.services.report_service import create_export, generate_export
        from tests.conftest import make_source

        user = _user(db)
        topic = _topic(db)
        _snapshot(db, topic, "US")
        src = make_source(db)
        article = _article(db, src)
        db.add(TopicArticle(topic_id=topic.id, article_id=article.id))
        db.flush()

        export = create_export(db, user.id, {
            "template": "topic_deep", "format": "pdf",
            "scope": {
                "topic_id": str(topic.id),
                "from": (NOW - timedelta(days=7)).strftime("%Y-%m-%d"),
                "to": NOW.strftime("%Y-%m-%d"),
            },
        })
        generate_export(db, export, tmp_path)
        assert export.status == "done"
        assert export.file_path and export.file_size > 500
        assert export.expires_at is not None
        with open(export.file_path, "rb") as f:
            content = f.read()
        assert content.startswith(b"%PDF")

    def test_process_pending_exports_notifies_user(self, db, tmp_path):
        from app.services.report_service import create_export, process_pending_exports

        user = _user(db)
        export = create_export(db, user.id, {
            "report_type": "periodic_weekly", "format": "docx",
            "params": {"countries": ["US"]},
            "time_range": {
                "from": (NOW - timedelta(days=7)).strftime("%Y-%m-%d"),
                "to": NOW.strftime("%Y-%m-%d"),
            },
        })
        assert export.status == "pending"
        processed = process_pending_exports(db, export_dir=tmp_path)
        assert processed == 1
        assert export.status == "done" and export.file_path.endswith(".docx")
        notice = db.query(Alert).filter(
            Alert.user_id == user.id,
            Alert.payload["kind"].astext == "report_export_done",
        ).all()
        assert len(notice) == 1
