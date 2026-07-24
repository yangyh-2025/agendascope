"""/internal/collect 集成测试：内部 token 鉴权、载荷校验、uuid/url_hash 幂等。"""
import uuid
from datetime import datetime, timezone

import pytest

from app.models.article import Article
from tests.conftest import make_source

pytestmark = pytest.mark.integration

TOKEN_HEADERS = {"Authorization": "Bearer test-internal-token"}


def _payload(source_id, **overrides):
    data = {
        "uuid": str(uuid.uuid4()),
        "source_id": str(source_id),
        "adapter_type": "rss",
        "url": f"https://example.com/news/{uuid.uuid4().hex[:8]}",
        "title": "某国央行宣布降息二十五个基点",
        "content": "某国央行今日宣布降息二十五个基点，以应对经济增长放缓压力，市场对此反应积极。" * 2,
        "informant": "Test Feed",
        "authors": ["Reporter A"],
        "pub_time": "2026-07-24T05:30:00Z",
    }
    data.update(overrides)
    return data


class TestInternalToken:
    def test_missing_token_401(self, client, db):
        source = make_source(db)
        db.commit()
        resp = client.post("/internal/collect", json=_payload(source.id))
        assert resp.status_code == 401
        assert resp.json()["code"] == 2001

    def test_wrong_token_401(self, client, db):
        source = make_source(db)
        db.commit()
        resp = client.post("/internal/collect", json=_payload(source.id),
                           headers={"Authorization": "Bearer wrong-token"})
        assert resp.status_code == 401


class TestValidation:
    def test_short_content_rejected(self, client, db):
        source = make_source(db)
        db.commit()
        resp = client.post("/internal/collect", json=_payload(source.id, content="太短"),
                           headers=TOKEN_HEADERS)
        assert resp.status_code == 400
        assert resp.json()["code"] == 1001

    def test_unknown_source_rejected(self, client, db):
        resp = client.post("/internal/collect", json=_payload(uuid.uuid4()), headers=TOKEN_HEADERS)
        assert resp.status_code == 400
        assert resp.json()["code"] == 1001


class TestIngest:
    def test_accept_and_article_written(self, client, db):
        source = make_source(db)
        db.commit()
        resp = client.post("/internal/collect", json=_payload(source.id), headers=TOKEN_HEADERS)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["accepted"] is True and data["duplicate"] is False

        article = db.query(Article).filter(Article.source_id == source.id).one()
        assert article.country_code == "US"
        assert article.time_source == "feed"
        assert article.visible_at is not None
        assert article.language == "en"
        assert article.source_channel == "rss"

    def test_uuid_idempotent(self, client, db):
        source = make_source(db)
        db.commit()
        payload = _payload(source.id)
        r1 = client.post("/internal/collect", json=payload, headers=TOKEN_HEADERS)
        r2 = client.post("/internal/collect", json=payload, headers=TOKEN_HEADERS)
        assert r1.json()["data"]["duplicate"] is False
        assert r2.json()["data"]["duplicate"] is True
        assert db.query(Article).count() == 1

    def test_url_hash_idempotent(self, client, db):
        source = make_source(db)
        db.commit()
        p1 = _payload(source.id)
        # 同一文章不同 uuid、URL 仅跟踪参数/锚点不同 → 规范化后 url_hash 相同
        p2 = _payload(source.id, url=p1["url"] + "?utm_source=rss#top")
        client.post("/internal/collect", json=p1, headers=TOKEN_HEADERS)
        r2 = client.post("/internal/collect", json=p2, headers=TOKEN_HEADERS)
        assert r2.json()["data"]["duplicate"] is True
        assert db.query(Article).count() == 1

    def test_no_pub_time_marks_crawled(self, client, db):
        source = make_source(db)
        db.commit()
        payload = _payload(source.id)
        payload.pop("pub_time")
        client.post("/internal/collect", json=payload, headers=TOKEN_HEADERS)
        article = db.query(Article).one()
        assert article.time_source == "crawled"
