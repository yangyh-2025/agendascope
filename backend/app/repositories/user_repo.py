"""users 数据访问。"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User


class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_username(self, username: str) -> User | None:
        return self.db.scalar(select(User).where(User.username == username))

    def get_by_id(self, user_id) -> User | None:
        return self.db.get(User, user_id)

    def create(self, user: User) -> User:
        self.db.add(user)
        self.db.flush()
        return user

    def record_login_success(self, user: User) -> None:
        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = datetime.now(timezone.utc)
        self.db.flush()

    def record_login_failure(self, user: User, lock_threshold: int, lock_minutes: int) -> bool:
        """失败计数+1，达阈值锁定 lock_minutes 分钟；返回是否本次触发锁定。"""
        from datetime import timedelta

        user.failed_login_count = (user.failed_login_count or 0) + 1
        locked = False
        if user.failed_login_count >= lock_threshold:
            user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=lock_minutes)
            user.failed_login_count = 0
            locked = True
        self.db.flush()
        return locked
