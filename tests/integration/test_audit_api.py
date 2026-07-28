"""审计日志查询/导出集成测试（T1.10）：repo 过滤分页 + 路由管理员限定 + CSV 导出。

路由模块未注册进 router.py（集成阶段接线 /system/audit-logs），测试侧独立挂载验证契约。
"""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.routes import audit as audit_route
from app.core.errors import register_exception_handlers
from app.core.security import hash_password
from app.db.session import get_db
from app.models.audit import AuditLog
from app.models.user import User
from app.repositories.audit_repo import query_audit_logs, write_audit

pytestmark = pytest.mark.integration


def _seed_audits(db, admin):
    write_audit(db, "auth.login", user=admin, ip="1.2.3.4")
    write_audit(db, "auth.login", user=admin, result="failure", detail={"username": "admin"})
    write_audit(db, "source.create", user=admin, resource="source:1")
    db.commit()


class TestQueryAuditLogs:
    def test_filter_by_action_and_result(self, db, admin_user):
        _seed_audits(db, admin_user)
        _, total = query_audit_logs(db, action="auth.login")
        assert total == 2
        items, total = query_audit_logs(db, action="auth.login", result="failure")
        assert total == 1
        assert items[0].result == "failure"

    def test_filter_by_actor_and_pagination(self, db, admin_user):
        _seed_audits(db, admin_user)
        page1, total = query_audit_logs(db, actor="admin", page=1, page_size=2)
        assert total == 3
        assert len(page1) == 2
        page2, _ = query_audit_logs(db, actor="admin", page=2, page_size=2)
        assert len(page2) == 1
        assert page1[0].at >= page1[1].at  # 时间倒序

    def test_filter_by_time_window(self, db, admin_user):
        _seed_audits(db, admin_user)
        future = datetime.now(UTC) + timedelta(hours=1)
        _, total = query_audit_logs(db, start=future)
        assert total == 0
        _, total = query_audit_logs(db, end=future)
        assert total == 3


@pytest.fixture()
def audit_client(client, db):
    """独立挂载审计路由的测试 app（复用 client 夹具的 Redis 指向与异常处理器契约）。"""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(audit_route.router, prefix="/system/audit-logs")

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestAuditRoutes:
    def test_admin_list_filter_and_export(self, audit_client, db, admin_user, auth_headers):
        _seed_audits(db, admin_user)

        resp = audit_client.get("/system/audit-logs", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["total"] >= 3
        assert body["data"]["page"] == 1

        filtered = audit_client.get(
            "/system/audit-logs?action=auth.login&result=failure", headers=auth_headers
        )
        assert filtered.status_code == 200
        items = filtered.json()["data"]["items"]
        assert len(items) == 1
        assert items[0]["action"] == "auth.login" and items[0]["result"] == "failure"

        export = audit_client.get("/system/audit-logs/export?action=auth.login", headers=auth_headers)
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("text/csv")
        assert "attachment" in export.headers["content-disposition"]
        lines = export.text.strip().splitlines()
        assert lines[0].lstrip("﻿").startswith("at,username,action")
        assert all("auth.login" in line for line in lines[1:])
        assert len(lines) >= 3  # 表头 + ≥2 条 auth.login（夹具登录 + 种子数据）

    def test_non_admin_forbidden(self, client, audit_client, db):
        reg = User(
            username="reg.audit",
            password_hash=hash_password("User12345A"),
            display_name="reg.audit",
            role="registered",
            must_change_password=False,
        )
        db.add(reg)
        db.commit()
        login = client.post("/api/v1/auth/login", json={"username": "reg.audit", "password": "User12345A"})
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
        resp = audit_client.get("/system/audit-logs", headers=headers)
        assert resp.status_code == 403
        assert resp.json()["code"] == 2002

    def test_invalid_result_param_400(self, audit_client, auth_headers):
        resp = audit_client.get("/system/audit-logs?result=bogus", headers=auth_headers)
        assert resp.status_code == 400
        assert resp.json()["code"] == 1001

    def test_audit_rows_actually_persisted(self, audit_client, db, admin_user, auth_headers):
        _seed_audits(db, admin_user)
        rows = db.scalars(select(AuditLog).where(AuditLog.action == "source.create")).all()
        assert len(rows) == 1
        assert rows[0].resource == "source:1"
