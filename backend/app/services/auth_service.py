"""认证服务：登录/刷新/登出（详细设计 1.4、7.1）。"""
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import (
    CODE_ACCOUNT_DISABLED,
    CODE_BAD_CREDENTIALS,
    CODE_PARAM_INVALID,
    CODE_RATE_LIMITED,
    CODE_UNAUTHORIZED,
    BizError,
)
from app.core.security import (
    TOKEN_TYPE_REFRESH,
    check_password_policy,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.session_store import (
    blacklist_access_token,
    hit_login_rate_limit,
    is_refresh_session_valid,
    register_refresh_session,
    revoke_all_user_sessions,
    revoke_refresh_session,
)
from app.models.user import User
from app.repositories.user_repo import UserRepository


class AuthService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = UserRepository(db)
        self.settings = get_settings()

    def _issue_pair(self, user: User) -> dict:
        access_token, _, access_exp = create_access_token(str(user.id), user.role)
        refresh_token, refresh_jti, refresh_exp = create_refresh_token(str(user.id))
        register_refresh_session(str(user.id), refresh_jti, int(refresh_exp.timestamp() - datetime.now(UTC).timestamp()))
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": int((access_exp - datetime.now(UTC)).total_seconds()),
        }

    def login(self, username: str, password: str, ip: str) -> tuple[dict, User, str]:
        """返回 (token_data, user, result)，失败抛 BizError。锁定与限流按等保要求。"""
        if hit_login_rate_limit(ip):
            raise BizError(CODE_RATE_LIMITED, "登录尝试过于频繁", {"retry_after": 60})

        user = self.repo.get_by_username(username)
        if user is None:
            raise BizError(CODE_BAD_CREDENTIALS, "用户名或密码错误")
        if user.status != "active":
            raise BizError(CODE_ACCOUNT_DISABLED, "账号已被禁用，请联系管理员")

        now = datetime.now(UTC)
        if user.locked_until and user.locked_until > now:
            retry_after = int((user.locked_until - now).total_seconds())
            raise BizError(CODE_RATE_LIMITED, "账号已锁定，请稍后再试", {"retry_after": retry_after})

        if not verify_password(password, user.password_hash):
            locked = self.repo.record_login_failure(
                user, self.settings.login_lock_threshold, self.settings.login_lock_minutes
            )
            self.db.commit()
            if locked:
                raise BizError(
                    CODE_RATE_LIMITED,
                    "连续失败次数过多，账号已锁定 15 分钟",
                    {"retry_after": self.settings.login_lock_minutes * 60},
                )
            raise BizError(CODE_BAD_CREDENTIALS, "用户名或密码错误")

        self.repo.record_login_success(user)
        token_data = self._issue_pair(user)
        self.db.commit()
        return token_data, user, "success"

    def refresh(self, refresh_token: str) -> dict:
        payload = decode_token(refresh_token, TOKEN_TYPE_REFRESH)
        user_id = payload["sub"]
        jti = payload["jti"]

        if not is_refresh_session_valid(user_id, jti):
            # 旧 refresh token 重放：全部会话强制失效（防盗用，详细设计 7.1）
            revoke_all_user_sessions(user_id)
            raise BizError(CODE_UNAUTHORIZED, "refresh_token 已失效，请重新登录")

        user = self.repo.get_by_id(user_id)
        if user is None or user.status != "active":
            raise BizError(CODE_UNAUTHORIZED, "用户不可用，请重新登录")

        revoke_refresh_session(user_id, jti)  # 一次性轮换：旧 token 立即失效
        token_data = self._issue_pair(user)
        self.db.commit()
        return token_data

    def logout(self, access_jti: str, access_exp: int, refresh_token: str) -> None:
        payload = decode_token(refresh_token, TOKEN_TYPE_REFRESH)
        revoke_refresh_session(payload["sub"], payload["jti"])
        blacklist_access_token(access_jti, access_exp)

    def change_password(self, user: User, old_password: str, new_password: str) -> None:
        """修改密码（T1.7 密码策略闭环）：

        旧密码校验 → 新密码过 check_password_policy（≥10 字符含大小写+数字）→
        落库并清除 must_change_password → 吊销该用户全部 refresh 会话，强制重新登录。
        """
        if not verify_password(old_password, user.password_hash):
            raise BizError(CODE_BAD_CREDENTIALS, "原密码错误")
        if not check_password_policy(new_password):
            raise BizError(CODE_PARAM_INVALID, "新密码不符合策略：至少 10 位且包含大小写字母与数字")
        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        self.db.flush()
        revoke_all_user_sessions(str(user.id))
        self.db.commit()
