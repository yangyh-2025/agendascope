"""安全组件单元测试：密码策略/bcrypt/JWT/SSRF 防护。"""
import pytest

from app.core.errors import BizError
from app.core.security import (
    TOKEN_TYPE_ACCESS,
    check_password_policy,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.ssrf import validate_public_url


class TestPasswordPolicy:
    @pytest.mark.parametrize("pwd", ["Abc1234567", "Xy9zBcDefg1", "Passw0rd!xx"])
    def test_valid(self, pwd):
        assert check_password_policy(pwd)

    @pytest.mark.parametrize("pwd", ["abc1234567", "ABC1234567", "Abcdefghij", "Ab12345"])
    def test_invalid(self, pwd):
        assert not check_password_policy(pwd)

    def test_bcrypt_roundtrip_cost12(self):
        h = hash_password("Abc1234567")
        assert h.startswith("$2b$12$")
        assert verify_password("Abc1234567", h)
        assert not verify_password("Wrong12345", h)


class TestJwt:
    def test_access_roundtrip(self):
        token, jti, exp = create_access_token("11111111-1111-1111-1111-111111111111", "admin")
        payload = decode_token(token, TOKEN_TYPE_ACCESS)
        assert payload["sub"] == "11111111-1111-1111-1111-111111111111"
        assert payload["role"] == "admin"
        assert payload["jti"] == jti

    def test_wrong_type_rejected(self):
        token, _, _ = create_refresh_token("u1")
        with pytest.raises(BizError):
            decode_token(token, TOKEN_TYPE_ACCESS)

    def test_tampered_rejected(self):
        token, _, _ = create_access_token("u1", "admin")
        with pytest.raises(BizError):
            decode_token(token[:-4] + "aaaa", TOKEN_TYPE_ACCESS)


class TestSsrf:
    def test_non_http_scheme_rejected(self):
        with pytest.raises(BizError) as exc:
            validate_public_url("ftp://example.com/x", resolve_dns=False)
        assert exc.value.code == 1002

    def test_private_ip_rejected(self):
        for url in ("http://192.168.1.1/x", "http://127.0.0.1:8080/", "http://10.0.0.5/"):
            with pytest.raises(BizError):
                validate_public_url(url, resolve_dns=False)

    def test_public_url_accepted(self):
        assert validate_public_url("https://example.com/news", resolve_dns=False) == "https://example.com/news"
