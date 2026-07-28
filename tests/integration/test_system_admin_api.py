"""系统管理后台 API 集成测试（T5.10/T5.13）：概览 P95、日志、许可、诊断包、磁盘清理。

需要本地 docker compose 的 postgres/redis；不可达时自动跳过。
"""
import io
import json
import os
import zipfile
from datetime import UTC, datetime, timedelta

import pytest

from app.models.system_state import SystemLicense
from app.services import maintenance_service
from app.services.license_service import sign_license_payload

SECRET = "test-license-secret"


def _code(**payload_kw):
    payload = {
        "license_id": "LIC-TEST-1",
        "product": "AgendaScope-EE",
        "expires_at": (datetime.now(UTC) + timedelta(days=20)).date().isoformat(),
    }
    payload.update(payload_kw)
    return sign_license_payload(payload, SECRET)


def test_overview_fields(client, auth_headers):
    resp = client.get("/api/v1/system/overview", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["metrics_status"] in ("ok", "unavailable")
    assert "latency_p95_min_24h" in data
    assert isinstance(data["latency_by_channel_24h"], list)
    assert "queue_backlog_raw_articles" in data


def test_overview_requires_admin(client):
    assert client.get("/api/v1/system/overview").status_code == 401


def test_logs_endpoint_requires_log_file(client, auth_headers):
    # 测试环境默认未配置 LOG_FILE_PATH → 明确报错而非假数据
    resp = client.get("/api/v1/system/logs", headers=auth_headers)
    assert resp.status_code == 422
    assert resp.json()["code"] == 4004


def test_logs_endpoint_reads_file(client, auth_headers, tmp_path, monkeypatch):
    from app.config import get_settings

    log = tmp_path / "app.log"
    log.write_text(
        json.dumps({"level": "info", "event": "boot"}) + "\n"
        + json.dumps({"level": "error", "event": "boom"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(get_settings(), "log_file_path", str(log))
    resp = client.get("/api/v1/system/logs?level=ERROR&lines=50", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["matched"] == 1
    assert "boom" in data["items"][0]


def test_license_flow(client, auth_headers):
    # 社区版默认状态
    resp = client.get("/api/v1/system/license", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "community"

    # 录入有效授权码
    resp = client.post("/api/v1/system/license", headers=auth_headers, json={"code": _code()})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "active"
    assert data["license_id"] == "LIC-TEST-1"
    assert data["reminder_level"] == "30d"
    assert data["write_allowed"] is True

    # 同码重复录入幂等
    resp = client.post("/api/v1/system/license", headers=auth_headers, json={"code": _code()})
    assert resp.status_code == 200

    # 签名非法拒绝
    resp = client.post("/api/v1/system/license", headers=auth_headers, json={"code": _code() + "0"})
    assert resp.status_code == 400

    # 已过期授权码拒绝录入
    expired_code = _code(expires_at=(datetime.now(UTC) - timedelta(days=1)).date().isoformat())
    resp = client.post("/api/v1/system/license", headers=auth_headers, json={"code": expired_code})
    assert resp.status_code == 422


def test_license_expired_blocks_write(client, db):
    """到期许可：require_license_active 依赖拒绝写（4006），无许可记录放行。"""
    from app.api.deps import require_license_active
    from app.core.errors import BizError

    require_license_active(db)  # 无许可记录（社区版）不抛

    db.add(SystemLicense(
        code_hash="f" * 64,
        payload={"license_id": "LIC-OLD", "product": "AgendaScope-EE"},
        expires_at=datetime.now(UTC) - timedelta(days=1),
    ))
    db.flush()
    with pytest.raises(BizError) as exc:
        require_license_active(db)
    assert exc.value.code == 4006


def test_diagnostics_zip(client, auth_headers):
    resp = client.post("/api/v1/system/diagnostics", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = set(zf.namelist())
    assert {"meta.json", "config_snapshot.json", "health.json", "db_counts.json"} <= names

    config = json.loads(zf.read("config_snapshot.json"))
    assert config["jwt_secret_key"] == "***"
    assert config["seed_admin_password"] == "***"
    assert config["license_secret_key"] == "***"
    assert "agenda_dev_pwd" not in config["database_url"]

    counts = json.loads(zf.read("db_counts.json"))
    assert "articles" in counts and "users" in counts

    health = json.loads(zf.read("health.json"))
    assert "components" in health or health.get("status") == "error"


def test_diagnostics_requires_admin(client):
    assert client.post("/api/v1/system/diagnostics").status_code == 401


def test_disk_cleanup_writes_alert(client, db, tmp_path, monkeypatch):
    """磁盘超阈值：清理过期原始文件并写站内告警。"""
    from app.config import get_settings
    from app.models.alert import Alert

    old = tmp_path / "old_raw.html"
    old.write_text("<html>raw</html>", encoding="utf-8")
    mtime = (datetime.now(UTC) - timedelta(days=120)).timestamp()
    os.utime(old, (mtime, mtime))

    settings = get_settings()
    monkeypatch.setattr(settings, "raw_html_dir", str(tmp_path))
    monkeypatch.setattr(settings, "gdelt_buffer_dir", str(tmp_path / "nope"))
    monkeypatch.setattr(maintenance_service, "disk_usage_percent", lambda path="/": 92.0)

    result = maintenance_service.run_disk_cleanup(db)
    assert result["triggered"] is True
    assert result["deleted"] == 1
    assert not old.exists()

    alert = db.query(Alert).filter(Alert.payload["type"].astext == "disk_cleanup").one()
    assert alert.payload["disk_percent"] == 92.0
    assert alert.payload["deleted"] == 1
