"""认证端点：登录/刷新/登出/当前用户（详细设计 1.4）。"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import ok
from app.core.security import TOKEN_TYPE_ACCESS, decode_token
from app.db.session import get_db
from app.models.user import User
from app.repositories.audit_repo import write_audit
from app.schemas.auth import ChangePasswordRequest, LoginRequest, LogoutRequest, RefreshRequest
from app.services.auth_service import AuthService

router = APIRouter()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/login")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    service = AuthService(db)
    ip = _client_ip(request)
    try:
        token_data, user, _ = service.login(body.username, body.password, ip)
    except Exception as exc:
        write_audit(db, "auth.login", detail={"username": body.username}, ip=ip,
                    user_agent=request.headers.get("user-agent", ""), result="failure")
        db.commit()
        raise exc
    write_audit(db, "auth.login", user=user, ip=ip, user_agent=request.headers.get("user-agent", ""))
    db.commit()
    return ok({
        **token_data,
        "user": {
            "id": str(user.id),
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
            "must_change_password": user.must_change_password,
        },
    })


@router.post("/refresh")
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    token_data = AuthService(db).refresh(body.refresh_token)
    return ok(token_data)


@router.post("/logout")
def logout(
    body: LogoutRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    auth_header = request.headers.get("authorization", "")
    access_token = auth_header.removeprefix("Bearer ").strip()
    payload = decode_token(access_token, TOKEN_TYPE_ACCESS)
    AuthService(db).logout(payload["jti"], payload["exp"], body.refresh_token)
    write_audit(db, "auth.logout", user=user, ip=_client_ip(request),
                user_agent=request.headers.get("user-agent", ""))
    db.commit()
    return ok(None)


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return ok({
        "id": str(user.id),
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "role": user.role,
        "locale": user.locale,
        "timezone": user.timezone,
        "must_change_password": user.must_change_password,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    })


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """修改密码：旧密码校验 + 服务端密码策略强制 + 吊销全部 refresh 会话强制重登。"""
    AuthService(db).change_password(user, body.old_password, body.new_password)
    write_audit(db, "auth.change_password", user=user, ip=_client_ip(request),
                user_agent=request.headers.get("user-agent", ""))
    db.commit()
    return ok({"must_change_password": False}, "密码已更新，请使用新密码重新登录")
