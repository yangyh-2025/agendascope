"""认证 API 集成测试：登录/锁定/刷新轮换/重放吊销/登出/RBAC。"""
import uuid

import pytest

from app.core.security import hash_password
from app.models.user import User

pytestmark = pytest.mark.integration


def _create_user(db, username, role="registered", password="User12345A"):
    user = User(
        username=username,
        password_hash=hash_password(password),
        display_name=username,
        role=role,
        must_change_password=False,
    )
    db.add(user)
    db.commit()
    return user


class TestLogin:
    def test_login_success(self, client, db):
        _create_user(db, "zhang.san")
        resp = client.post("/api/v1/auth/login", json={"username": "zhang.san", "password": "User12345A"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        assert data["access_token"] and data["refresh_token"]
        assert data["expires_in"] > 0
        assert data["user"]["role"] == "registered"

    def test_login_wrong_password_2003(self, client, db):
        _create_user(db, "li.si")
        resp = client.post("/api/v1/auth/login", json={"username": "li.si", "password": "WrongPass1"})
        assert resp.status_code == 401
        assert resp.json()["code"] == 2003

    def test_login_lock_after_5_failures(self, client, db):
        _create_user(db, "wang.wu")
        for _ in range(5):
            client.post("/api/v1/auth/login", json={"username": "wang.wu", "password": "WrongPass1"})
        resp = client.post("/api/v1/auth/login", json={"username": "wang.wu", "password": "User12345A"})
        assert resp.status_code == 429
        assert resp.json()["code"] == 5001

    def test_disabled_account_2004(self, client, db):
        user = _create_user(db, "disabled.user")
        user.status = "disabled"
        db.commit()
        resp = client.post("/api/v1/auth/login", json={"username": "disabled.user", "password": "User12345A"})
        assert resp.status_code == 403
        assert resp.json()["code"] == 2004


class TestRefreshRotation:
    def test_refresh_rotates_and_old_replay_revokes_all(self, client, db):
        _create_user(db, "refresh.user")
        login = client.post("/api/v1/auth/login", json={"username": "refresh.user", "password": "User12345A"})
        tokens = login.json()["data"]

        # 正常轮换：旧 refresh 换新对
        r1 = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert r1.status_code == 200
        new_tokens = r1.json()["data"]

        # 旧 refresh 重放 → 401 且全部会话吊销
        replay = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert replay.status_code == 401

        # 新 refresh 也被吊销（防盗用）
        r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]})
        assert r2.status_code == 401

    def test_access_token_usable_after_refresh(self, client, db):
        _create_user(db, "me.user")
        login = client.post("/api/v1/auth/login", json={"username": "me.user", "password": "User12345A"})
        tokens = login.json()["data"]
        r = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        new_access = r.json()["data"]["access_token"]
        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new_access}"})
        assert me.status_code == 200
        assert me.json()["data"]["username"] == "me.user"


class TestLogout:
    def test_logout_blacklists_access_and_revokes_refresh(self, client, db):
        _create_user(db, "logout.user")
        login = client.post("/api/v1/auth/login", json={"username": "logout.user", "password": "User12345A"})
        tokens = login.json()["data"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        out = client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}, headers=headers)
        assert out.status_code == 200

        me = client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 401
        r = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert r.status_code == 401


class TestRbac:
    def test_unauthenticated_401(self, client):
        resp = client.get("/api/v1/sources")
        assert resp.status_code == 401
        assert resp.json()["code"] == 2001

    def test_registered_forbidden_admin_endpoint(self, client, db):
        _create_user(db, "reg.user")
        login = client.post("/api/v1/auth/login", json={"username": "reg.user", "password": "User12345A"})
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
        resp = client.post("/api/v1/sources", json={
            "name": "X", "country_code": "US", "homepage_url": "https://x.com",
            "media_type": "online", "language": "en",
        }, headers=headers)
        assert resp.status_code == 403
        assert resp.json()["code"] == 2002

    def test_admin_can_list(self, client, auth_headers):
        resp = client.get("/api/v1/sources", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["code"] == 0
