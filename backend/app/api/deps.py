"""FastAPI 依赖：当前用户注入与 RBAC 角色门禁（详细设计 7.2）。

角色档位（由低到高）：registered < authorized < admin；guest 无账号不落库。
"""
import uuid

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import CODE_ACCOUNT_DISABLED, CODE_FORBIDDEN, CODE_UNAUTHORIZED, BizError
from app.core.security import TOKEN_TYPE_ACCESS, decode_token
from app.core.session_store import is_access_blacklisted
from app.db.session import get_db
from app.models.user import User

ROLE_REGISTERED = "registered"
ROLE_AUTHORIZED = "authorized"
ROLE_ADMIN = "admin"
_ROLE_LEVEL = {ROLE_REGISTERED: 1, ROLE_AUTHORIZED: 2, ROLE_ADMIN: 3}

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise BizError(CODE_UNAUTHORIZED, "缺少认证令牌")
    payload = decode_token(credentials.credentials, TOKEN_TYPE_ACCESS)
    if is_access_blacklisted(payload["jti"]):
        raise BizError(CODE_UNAUTHORIZED, "Token 已吊销，请重新登录")
    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, AttributeError):
        raise BizError(CODE_UNAUTHORIZED, "Token 主体非法") from None
    user = db.get(User, user_id)
    if user is None:
        raise BizError(CODE_UNAUTHORIZED, "用户不存在或已删除")
    if user.status != "active":
        raise BizError(CODE_ACCOUNT_DISABLED, "账号已被禁用，请联系管理员")
    request.state.user = user
    return user


def require_role(min_role: str):
    """接口级鉴权依赖：角色档位不低于 min_role；越权一律 403。"""

    def checker(user: User = Depends(get_current_user)) -> User:
        if _ROLE_LEVEL.get(user.role, 0) < _ROLE_LEVEL[min_role]:
            raise BizError(CODE_FORBIDDEN, "当前角色无权访问该资源")
        return user

    return checker
