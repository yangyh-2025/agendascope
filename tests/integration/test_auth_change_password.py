"""改密闭环集成测试（T1.7）：强制改密拦截 / 密码策略强制 / 会话吊销 / 审计留痕。"""
import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models.audit import AuditLog
from app.models.user import User

pytestmark = pytest.mark.integration


def _create_user(db, username, password="User12345A", must_change=True):
    user = User(
        username=username,
        password_hash=hash_password(password),
        display_name=username,
        role="registered",
        must_change_password=must_change,
    )
    db.add(user)
    db.commit()
    return user


def _login(client, username, password):
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


class TestMustChangePasswordGate:
    def test_business_endpoint_rejected_until_password_changed(self, client, db):
        _create_user(db, "must.change")
        tokens = _login(client, "must.change", "User12345A")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        # 业务接口拒绝：专用错误码 2005
        blocked = client.get("/api/v1/sources", headers=headers)
        assert blocked.status_code == 403
        assert blocked.json()["code"] == 2005

        # me / change-password / logout 不受限
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

        chg = client.post(
            "/api/v1/auth/change-password",
            json={"old_password": "User12345A", "new_password": "NewPass12345"},
            headers=headers,
        )
        assert chg.status_code == 200
        assert chg.json()["data"]["must_change_password"] is False

        # 改密后业务接口放行
        assert client.get("/api/v1/sources", headers=headers).status_code == 200

    def test_weak_new_password_rejected(self, client, db):
        _create_user(db, "weak.new")
        tokens = _login(client, "weak.new", "User12345A")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        for weak in ("short", "alllowercase1", "ALLUPPERCASE1", "NoDigitsHere"):
            resp = client.post(
                "/api/v1/auth/change-password",
                json={"old_password": "User12345A", "new_password": weak},
                headers=headers,
            )
            assert resp.status_code == 400, weak
            assert resp.json()["code"] == 1001

    def test_wrong_old_password_rejected(self, client, db):
        _create_user(db, "wrong.old")
        tokens = _login(client, "wrong.old", "User12345A")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        resp = client.post(
            "/api/v1/auth/change-password",
            json={"old_password": "WrongPass99", "new_password": "NewPass12345"},
            headers=headers,
        )
        assert resp.status_code == 401
        assert resp.json()["code"] == 2003

    def test_refresh_revoked_and_audit_written(self, client, db):
        user = _create_user(db, "revoke.me")
        tokens = _login(client, "revoke.me", "User12345A")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        chg = client.post(
            "/api/v1/auth/change-password",
            json={"old_password": "User12345A", "new_password": "NewPass12345"},
            headers=headers,
        )
        assert chg.status_code == 200

        # 全部 refresh 会话吊销，强制重新登录
        refresh = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert refresh.status_code == 401

        # must_change_password 落库为 False
        db.expire_all()
        assert db.get(User, user.id).must_change_password is False

        # 审计留痕
        entry = db.scalar(select(AuditLog).where(AuditLog.action == "auth.change_password"))
        assert entry is not None
        assert entry.username == "revoke.me"
        assert entry.result == "success"

        # 新密码可登录
        _login(client, "revoke.me", "NewPass12345")
