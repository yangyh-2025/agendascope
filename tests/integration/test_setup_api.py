"""安装向导 API 集成测试（T5.6）：落库生效、状态查询、初始化完成关闭与幂等。

需要本地 docker compose 的 postgres/redis；不可达时自动跳过。
"""
from tests.conftest import make_source


def test_wizard_full_flow(client, db):
    # 初始状态：未初始化，从 Step 1 开始，三阶段进度结构齐全
    resp = client.get("/api/v1/setup/status")
    assert resp.status_code == 200
    status = resp.json()["data"]
    assert status["initialized"] is False
    assert status["current_step"] == 1
    assert [s["key"] for s in status["progress"]["stages"]] == [
        "seed_sources", "history_backfill", "first_clustering",
    ]

    # Step 2：基础配置真实落库
    resp = client.post("/api/v1/setup", json={"step": 2, "app_name": "观澜测试实例"})
    assert resp.status_code == 200
    assert resp.json()["data"]["app_name"] == "观澜测试实例"
    status = client.get("/api/v1/setup/status").json()["data"]
    assert status["app_name"] == "观澜测试实例"
    assert status["current_step"] == 3
    assert status["completed_steps"] == [2]

    # Step 3：监控范围落库并作用于源启用状态（未勾选国家 disabled，ZZ 伪源不动）
    us = make_source(db, country_code="US")
    jp = make_source(db, country_code="JP")
    zz = make_source(db, country_code="ZZ", name="GDELT 兜底通道", feed_url=None)
    db.commit()
    resp = client.post("/api/v1/setup", json={"step": 3, "countries": ["us"]})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["countries"] == ["US"]
    assert data["sources_disabled"] == 1
    db.refresh(us)
    db.refresh(jp)
    db.refresh(zz)
    assert us.status == "active"
    assert jp.status == "disabled"
    assert zz.status == "active"

    # 重新勾选日本：disabled 源恢复 active（幂等可逆）
    resp = client.post("/api/v1/setup", json={"step": 3, "countries": ["US", "JP"]})
    assert resp.json()["data"]["sources_enabled"] == 1
    db.refresh(jp)
    assert jp.status == "active"

    # Step 4：管理员密码真实生效
    resp = client.post("/api/v1/setup", json={
        "step": 4, "admin_username": "admin", "admin_password": "WizardPass123",
    })
    assert resp.status_code == 200, resp.text
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "WizardPass123"})
    assert login.status_code == 200, login.text

    # Step 5：完成初始化
    resp = client.post("/api/v1/setup", json={"step": 5})
    assert resp.status_code == 200
    assert resp.json()["data"]["initialized"] is True
    status = client.get("/api/v1/setup/status").json()["data"]
    assert status["initialized"] is True
    assert status["current_step"] == 5

    # 初始化完成后写端点关闭（专用错误码 4005 / HTTP 409），Step 4 不再重置密码
    for step_body in (
        {"step": 2, "app_name": "x"},
        {"step": 3, "countries": ["US"]},
        {"step": 4, "admin_username": "admin", "admin_password": "OtherPass123"},
        {"step": 5},
    ):
        resp = client.post("/api/v1/setup", json=step_body)
        assert resp.status_code == 409
        assert resp.json()["code"] == 4005
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "WizardPass123"})
    assert login.status_code == 200


def test_step5_requires_step4(client):
    resp = client.post("/api/v1/setup", json={"step": 5})
    assert resp.status_code == 422
    assert resp.json()["code"] == 4002


def test_step3_validation(client):
    resp = client.post("/api/v1/setup", json={"step": 3, "countries": []})
    assert resp.status_code == 400
    resp = client.post("/api/v1/setup", json={"step": 3, "countries": ["USA"]})
    assert resp.status_code == 400


def test_step4_password_policy(client):
    resp = client.post("/api/v1/setup", json={
        "step": 4, "admin_username": "admin", "admin_password": "weak",
    })
    assert resp.status_code == 400


def test_status_progress_reflects_counts(client, db):
    make_source(db, country_code="US")
    db.commit()
    status = client.get("/api/v1/setup/status").json()["data"]
    stages = {s["key"]: s for s in status["progress"]["stages"]}
    assert stages["seed_sources"]["done"] is True
    assert stages["seed_sources"]["count"] >= 1
    assert stages["history_backfill"]["done"] is False
    assert status["progress"]["overall_percent"] == 33
