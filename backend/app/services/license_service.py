"""企业许可服务（T5.10）：HMAC 签名授权码录入 + 到期三级提醒 + 到期只读判定。

授权码格式：AGS1.<payload_b64url>.<hmac_sha256_hex>
- payload JSON：{"license_id", "product", "expires_at": "YYYY-MM-DD", "issued_at"?}
- 签名密钥由配置 LICENSE_SECRET_KEY 注入；库内只存授权码 SHA-256 哈希，不落明文
到期提醒级别：>30 天 none；≤30 天 30d；≤7 天 7d；≤1 天 1d；已到期 expired。
"""
import base64
import hashlib
import hmac
import json
from datetime import UTC, date, datetime, time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import (
    CODE_DEPENDENCY_DEGRADED,
    CODE_PARAM_INVALID,
    CODE_STATE_INVALID,
    BizError,
)
from app.models.system_state import SystemLicense

CODE_PREFIX = "AGS1"

REMINDER_NONE = "none"
REMINDER_EXPIRED = "expired"


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def sign_license_payload(payload: dict, secret: str) -> str:
    """签发授权码（部署方/厂商侧工具使用；服务端只验签）。"""
    body = _b64url_encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{CODE_PREFIX}.{body}.{sig}"


def code_digest(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def verify_license_code(code: str, secret: str) -> dict:
    """校验授权码格式与 HMAC 签名，返回 payload；非法即抛 BizError(1001)。"""
    if not secret:
        raise BizError(CODE_DEPENDENCY_DEGRADED, "服务端未配置 LICENSE_SECRET_KEY，无法录入授权码")
    parts = (code or "").strip().split(".")
    if len(parts) != 3 or parts[0] != CODE_PREFIX:
        raise BizError(CODE_PARAM_INVALID, "授权码格式非法（应为 AGS1.<payload>.<signature>）")
    _, body, sig = parts
    expect = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, sig.lower()):
        raise BizError(CODE_PARAM_INVALID, "授权码签名校验失败")
    try:
        payload = json.loads(_b64url_decode(body))
    except (ValueError, UnicodeDecodeError):
        raise BizError(CODE_PARAM_INVALID, "授权码负载解析失败") from None
    if not isinstance(payload, dict) or not payload.get("license_id") or not payload.get("product"):
        raise BizError(CODE_PARAM_INVALID, "授权码负载缺少 license_id/product 字段")
    try:
        expires_on = date.fromisoformat(str(payload["expires_at"]))
    except (KeyError, ValueError):
        raise BizError(CODE_PARAM_INVALID, "授权码 expires_at 非法（应为 YYYY-MM-DD）") from None
    payload["expires_on"] = expires_on.isoformat()
    return payload


def _expires_at_utc(payload: dict) -> datetime:
    """到期日按当日 23:59:59 UTC 收敛。"""
    return datetime.combine(date.fromisoformat(payload["expires_on"]), time(23, 59, 59), tzinfo=UTC)


def enroll_license(db: Session, code: str, secret: str, user_id: UUID | None) -> SystemLicense:
    """录入授权码：验签 → 存哈希与声明负载；同码重复录入幂等返回已登记记录。"""
    payload = verify_license_code(code, secret)
    expires_at = _expires_at_utc(payload)
    if expires_at <= datetime.now(UTC):
        raise BizError(CODE_STATE_INVALID, "授权码已过期，请联系供应商续期后再录入")
    digest = code_digest(code)
    existing = db.scalar(select(SystemLicense).where(SystemLicense.code_hash == digest))
    if existing is not None:
        return existing
    payload.pop("expires_on", None)
    row = SystemLicense(
        code_hash=digest,
        payload={"license_id": payload["license_id"], "product": payload["product"],
                 "expires_at": payload["expires_at"], "issued_at": payload.get("issued_at")},
        expires_at=expires_at,
        activated_by=user_id,
    )
    db.add(row)
    db.flush()
    return row


def get_current_license(db: Session) -> SystemLicense | None:
    """当前生效许可：最近一次录入的授权码。"""
    return db.scalar(
        select(SystemLicense).order_by(SystemLicense.activated_at.desc()).limit(1)
    )


def reminder_level(expires_at: datetime, now: datetime) -> str:
    """到期三级提醒：>30 天 none；≤30/7/1 天分别 30d/7d/1d；已到期 expired。"""
    remaining = expires_at - now
    if remaining.total_seconds() <= 0:
        return REMINDER_EXPIRED
    if remaining.days < 1:
        return "1d"
    if remaining.days < 7:
        return "7d"
    if remaining.days < 30:
        return "30d"
    return REMINDER_NONE


def is_write_allowed(license_row: SystemLicense | None, now: datetime) -> bool:
    """到期只读判定：无许可记录（社区版）或未到期放行；到期拒绝写、保留读与数据。"""
    if license_row is None:
        return True
    return license_row.expires_at > now


def license_status(license_row: SystemLicense | None, now: datetime) -> dict:
    """GET /system/license 响应体。"""
    if license_row is None:
        return {
            "status": "community",
            "license_id": None,
            "product": None,
            "expires_at": None,
            "days_remaining": None,
            "reminder_level": REMINDER_NONE,
            "write_allowed": True,
            "note": "社区版无许可到期限制；企业版请录入授权码",
        }
    expired = license_row.expires_at <= now
    days_remaining = max(0, (license_row.expires_at - now).days)
    return {
        "status": "expired" if expired else "active",
        "license_id": license_row.payload.get("license_id"),
        "product": license_row.payload.get("product"),
        "expires_at": license_row.expires_at.isoformat(),
        "days_remaining": days_remaining,
        "reminder_level": reminder_level(license_row.expires_at, now),
        "write_allowed": not expired,
        "activated_at": license_row.activated_at.isoformat() if license_row.activated_at else None,
    }
