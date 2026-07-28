"""许可服务单元测试（T5.10）：签发/验签、三级提醒、到期只读判定。"""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.errors import (
    CODE_DEPENDENCY_DEGRADED,
    CODE_PARAM_INVALID,
    BizError,
)
from app.services.license_service import (
    is_write_allowed,
    license_status,
    reminder_level,
    sign_license_payload,
    verify_license_code,
)

SECRET = "unit-test-license-secret"
NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=UTC)


def _payload(**kw):
    base = {"license_id": "LIC-001", "product": "AgendaScope-EE", "expires_at": "2027-07-28"}
    base.update(kw)
    return base


class TestSignAndVerify:
    def test_roundtrip(self):
        code = sign_license_payload(_payload(), SECRET)
        parsed = verify_license_code(code, SECRET)
        assert parsed["license_id"] == "LIC-001"
        assert parsed["product"] == "AgendaScope-EE"
        assert parsed["expires_on"] == "2027-07-28"

    def test_bad_signature_rejected(self):
        code = sign_license_payload(_payload(), SECRET)
        body, sig = code.rsplit(".", 1)
        tampered = f"{body}.{'0' * 64}"
        with pytest.raises(BizError) as exc:
            verify_license_code(tampered, SECRET)
        assert exc.value.code == CODE_PARAM_INVALID

    def test_wrong_secret_rejected(self):
        code = sign_license_payload(_payload(), SECRET)
        with pytest.raises(BizError):
            verify_license_code(code, "another-secret")

    def test_malformed_code_rejected(self):
        for bad in ("", "AGS1.onlytwo.part", "XXX.a.b", "AGS1..sig"):
            with pytest.raises(BizError):
                verify_license_code(bad, SECRET)

    def test_empty_secret_rejected(self):
        code = sign_license_payload(_payload(), SECRET)
        with pytest.raises(BizError) as exc:
            verify_license_code(code, "")
        assert exc.value.code == CODE_DEPENDENCY_DEGRADED

    def test_payload_missing_fields_rejected(self):
        code = sign_license_payload({"license_id": "LIC-001"}, SECRET)
        with pytest.raises(BizError):
            verify_license_code(code, SECRET)

    def test_bad_expires_at_rejected(self):
        code = sign_license_payload(_payload(expires_at="not-a-date"), SECRET)
        with pytest.raises(BizError):
            verify_license_code(code, SECRET)


class TestReminderLevel:
    def test_expired(self):
        assert reminder_level(NOW - timedelta(seconds=1), NOW) == "expired"

    def test_within_1_day(self):
        assert reminder_level(NOW + timedelta(hours=12), NOW) == "1d"

    def test_within_7_days(self):
        assert reminder_level(NOW + timedelta(days=3), NOW) == "7d"

    def test_within_30_days(self):
        assert reminder_level(NOW + timedelta(days=15), NOW) == "30d"

    def test_beyond_30_days(self):
        assert reminder_level(NOW + timedelta(days=60), NOW) == "none"


class TestWriteAllowed:
    def test_no_license_is_community_allowed(self):
        assert is_write_allowed(None, NOW) is True

    def test_active_license_allowed(self):
        row = SimpleNamespace(expires_at=NOW + timedelta(days=10))
        assert is_write_allowed(row, NOW) is True

    def test_expired_license_blocked(self):
        row = SimpleNamespace(expires_at=NOW - timedelta(days=1))
        assert is_write_allowed(row, NOW) is False


class TestLicenseStatus:
    def test_community(self):
        status = license_status(None, NOW)
        assert status["status"] == "community"
        assert status["write_allowed"] is True
        assert status["reminder_level"] == "none"
        assert status["expires_at"] is None

    def test_active(self):
        row = SimpleNamespace(
            expires_at=NOW + timedelta(days=15),
            payload={"license_id": "LIC-001", "product": "AgendaScope-EE"},
            activated_at=NOW,
        )
        status = license_status(row, NOW)
        assert status["status"] == "active"
        assert status["reminder_level"] == "30d"
        assert status["write_allowed"] is True
        assert status["license_id"] == "LIC-001"

    def test_expired(self):
        row = SimpleNamespace(
            expires_at=NOW - timedelta(days=2),
            payload={"license_id": "LIC-001", "product": "AgendaScope-EE"},
            activated_at=None,
        )
        status = license_status(row, NOW)
        assert status["status"] == "expired"
        assert status["reminder_level"] == "expired"
        assert status["write_allowed"] is False
        assert status["activated_at"] is None
