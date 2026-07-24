"""sources API 集成测试：CRUD/列表过滤/覆盖率/冲突与状态机错误码。"""
import uuid

import pytest

from app.models.source import Source

pytestmark = pytest.mark.integration


def _payload(**overrides):
    data = {
        "name": "The Test Times",
        "name_zh": "测试时报",
        "country_code": "GB",
        "homepage_url": "https://test-times.example.com",
        "feed_url": "https://test-times.example.com/rss",
        "collect_mode": "rss",
        "adapter_type": "rss",
        "media_type": "newspaper",
        "language": "en",
        "poll_interval_min": 15,
        "audience_weight": 8.5,
        "coverage_confidence": "medium",
    }
    data.update(overrides)
    return data


class TestSourceCrud:
    def test_create_and_detail(self, client, auth_headers):
        resp = client.post("/api/v1/sources", json=_payload(), headers=auth_headers)
        assert resp.status_code == 200, resp.text
        source_id = resp.json()["data"]["id"]

        detail = client.get(f"/api/v1/sources/{source_id}", headers=auth_headers)
        assert detail.status_code == 200
        data = detail.json()["data"]
        assert data["name"] == "The Test Times"
        assert data["status"] == "active"
        assert data["crawl_config"] == {}  # admin 可见完整配置

    def test_create_duplicate_feed_url_4001(self, client, auth_headers):
        client.post("/api/v1/sources", json=_payload(), headers=auth_headers)
        resp = client.post("/api/v1/sources", json=_payload(name="Another"), headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["code"] == 4001
        assert "existing_source_id" in resp.json()["data"]

    def test_create_invalid_country_1001(self, client, auth_headers):
        resp = client.post("/api/v1/sources", json=_payload(country_code="gb1"), headers=auth_headers)
        assert resp.status_code == 400
        assert resp.json()["code"] == 1001

    def test_create_internal_url_1002(self, client, auth_headers):
        resp = client.post("/api/v1/sources", json=_payload(homepage_url="http://192.168.0.1/news"),
                           headers=auth_headers)
        assert resp.status_code == 400
        assert resp.json()["code"] == 1002

    def test_pipeline_requires_entry_points(self, client, auth_headers):
        resp = client.post("/api/v1/sources", json=_payload(
            feed_url=None, adapter_type="pipeline", crawl_config={}), headers=auth_headers)
        assert resp.status_code == 400
        assert resp.json()["code"] == 1001

    def test_update_and_audit(self, client, auth_headers, db):
        source_id = client.post("/api/v1/sources", json=_payload(), headers=auth_headers).json()["data"]["id"]
        resp = client.put(f"/api/v1/sources/{source_id}", json={"poll_interval_min": 30}, headers=auth_headers)
        assert resp.status_code == 200
        detail = client.get(f"/api/v1/sources/{source_id}", headers=auth_headers).json()["data"]
        assert detail["poll_interval_min"] == 30

        from app.models.audit import AuditLog

        actions = [a.action for a in db.query(AuditLog).all()]
        assert "source.create" in actions and "source.update" in actions

    def test_update_not_found_3001(self, client, auth_headers):
        resp = client.put(f"/api/v1/sources/{uuid.uuid4()}", json={"poll_interval_min": 10},
                          headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["code"] == 3001

    def test_failed_to_active_direct_4002(self, client, auth_headers, db):
        source_id = client.post("/api/v1/sources", json=_payload(), headers=auth_headers).json()["data"]["id"]
        source = db.get(Source, uuid.UUID(source_id))
        source.status = "failed"
        db.commit()
        resp = client.put(f"/api/v1/sources/{source_id}", json={"status": "active"}, headers=auth_headers)
        assert resp.status_code == 422
        assert resp.json()["code"] == 4002

    def test_verify_requires_failed_status(self, client, auth_headers):
        source_id = client.post("/api/v1/sources", json=_payload(), headers=auth_headers).json()["data"]["id"]
        resp = client.post(f"/api/v1/sources/{source_id}/verify", headers=auth_headers)
        assert resp.status_code == 422
        assert resp.json()["code"] == 4002


class TestSourceListAndCoverage:
    def test_list_filters(self, client, auth_headers):
        client.post("/api/v1/sources", json=_payload(), headers=auth_headers)
        client.post("/api/v1/sources", json=_payload(
            name="US Daily", country_code="US",
            feed_url="https://us-daily.example.com/rss", homepage_url="https://us-daily.example.com",
        ), headers=auth_headers)

        resp = client.get("/api/v1/sources?country_code=GB", headers=auth_headers)
        data = resp.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["country_code"] == "GB"
        assert data["country_summary"]["country_code"] == "GB"

        resp = client.get("/api/v1/sources?keyword=Daily", headers=auth_headers)
        assert resp.json()["data"]["total"] == 1

    def test_coverage(self, client, auth_headers):
        client.post("/api/v1/sources", json=_payload(audience_weight=75.0), headers=auth_headers)
        resp = client.get("/api/v1/sources/coverage", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["methodology"]
        gb = next(i for i in data["items"] if i["country_code"] == "GB")
        assert gb["total_audience_share"] == 0.75
        assert gb["coverage_gap"] is False
