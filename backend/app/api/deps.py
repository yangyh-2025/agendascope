"""FastAPI 依赖：当前用户注入与 RBAC 角色门禁（详细设计 7.2）。

角色档位（由低到高）：registered < authorized < admin；guest 无账号不落库。
"""
import uuid

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import (
    CODE_ACCOUNT_DISABLED,
    CODE_FORBIDDEN,
    CODE_LICENSE_EXPIRED,
    CODE_PASSWORD_CHANGE_REQUIRED,
    CODE_UNAUTHORIZED,
    BizError,
)
from app.core.security import TOKEN_TYPE_ACCESS, decode_token
from app.core.session_store import is_access_blacklisted
from app.db.session import get_db
from app.models.user import User

_es_client = None
_es_probed = False


def get_es():
    """Elasticsearch 客户端依赖：不可用/未配置时返回 None，路由走 PG 降级路径。"""
    global _es_client, _es_probed
    if _es_probed:
        return _es_client
    _es_probed = True
    try:
        from elasticsearch import Elasticsearch

        from app.config import get_settings

        client = Elasticsearch(get_settings().elasticsearch_url, request_timeout=5)
        if client.ping():
            _es_client = client
    except Exception:  # ES 不可达属预期降级场景，不抛出
        _es_client = None
    return _es_client

ROLE_REGISTERED = "registered"
ROLE_AUTHORIZED = "authorized"
ROLE_ADMIN = "admin"
_ROLE_LEVEL = {ROLE_REGISTERED: 1, ROLE_AUTHORIZED: 2, ROLE_ADMIN: 3}

_bearer = HTTPBearer(auto_error=False)

# must_change_password 强制改密闭环（T1.7）：除以下路径外，业务接口一律拒绝（2005）
_PASSWORD_CHANGE_ALLOWED_PATHS = frozenset({
    "/api/v1/auth/me",
    "/api/v1/auth/logout",
    "/api/v1/auth/change-password",
})


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
    if user.must_change_password and request.url.path not in _PASSWORD_CHANGE_ALLOWED_PATHS:
        raise BizError(
            CODE_PASSWORD_CHANGE_REQUIRED,
            "初始密码尚未修改，请先调用 /auth/change-password 完成改密",
        )
    request.state.user = user
    return user


def require_role(min_role: str):
    """接口级鉴权依赖：角色档位不低于 min_role；越权一律 403。"""

    def checker(user: User = Depends(get_current_user)) -> User:
        if _ROLE_LEVEL.get(user.role, 0) < _ROLE_LEVEL[min_role]:
            raise BizError(CODE_FORBIDDEN, "当前角色无权访问该资源")
        return user

    return checker


def require_license_active(db: Session = Depends(get_db)) -> None:
    """许可到期只读门禁（T5.10）：企业许可到期后业务写接口挂载此依赖拒绝写操作。

    无许可记录（社区版）或未到期一律放行；到期仅拒绝写，读接口与数据全部保留。
    接线：在业务写路由加 `Depends(require_license_active)`。
    """
    from datetime import UTC, datetime

    from app.services.license_service import get_current_license, is_write_allowed

    if not is_write_allowed(get_current_license(db), datetime.now(UTC)):
        raise BizError(CODE_LICENSE_EXPIRED, "许可已到期，系统进入只读模式（数据保留，写操作暂不可用）")
