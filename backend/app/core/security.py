"""密码散列与 JWT 签发/验证（详细设计 7.1）。

- 密码：bcrypt cost=12；策略 ≥10 字符且含大小写+数字
- JWT：access（默认 120min）+ refresh（默认 12h，一次性轮换，jti 会话态存 Redis）
"""
import re
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from app.config import get_settings
from app.core.errors import CODE_UNAUTHORIZED, BizError

_BCRYPT_ROUNDS = 12

_PWD_RE = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{10,}$")

TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"


def check_password_policy(password: str) -> bool:
    return bool(_PWD_RE.match(password))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def _encode(subject: str, token_type: str, expires_minutes: int, extra: dict | None = None) -> tuple[str, str, datetime]:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=expires_minutes)
    jti = str(uuid.uuid4())
    payload = {
        "sub": subject,
        "type": token_type,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        **(extra or {}),
    }
    settings = get_settings()
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm), jti, expires_at


def create_access_token(user_id: str, role: str) -> tuple[str, str, datetime]:
    settings = get_settings()
    return _encode(user_id, TOKEN_TYPE_ACCESS, settings.access_token_expire_minutes, {"role": role})


def create_refresh_token(user_id: str) -> tuple[str, str, datetime]:
    settings = get_settings()
    return _encode(user_id, TOKEN_TYPE_REFRESH, settings.refresh_token_expire_minutes)


def decode_token(token: str, expected_type: str) -> dict:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise BizError(CODE_UNAUTHORIZED, "Token 已过期或签名非法，请重新登录") from None
    if payload.get("type") != expected_type:
        raise BizError(CODE_UNAUTHORIZED, "Token 类型不正确")
    return payload
