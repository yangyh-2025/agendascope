"""API Key 管理（登录用户自助）：创建/列表/改名/吊销（详细设计 8.2）。

完整 Key 只在创建响应里返回一次，之后列表只显示 prefix。
"""
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.api_key_auth import generate_api_key
from app.core.errors import CODE_NOT_FOUND, BizError, ok
from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.user import User
from app.repositories.audit_repo import write_audit

router = APIRouter()


class CreateKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=600)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class UpdateKeyRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=600)


def _mask(key: ApiKey) -> dict:
    return {
        "id": str(key.id),
        "name": key.name,
        "prefix": key.prefix,
        "scopes": key.scopes,
        "rate_limit_per_minute": key.rate_limit_per_minute,
        "expires_at": key.expires_at.isoformat() if key.expires_at else None,
        "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
        "revoked_at": key.revoked_at.isoformat() if key.revoked_at else None,
        "created_at": key.created_at.isoformat() if key.created_at else None,
    }


@router.post("")
def create_key(
    body: CreateKeyRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    plain, key_hash, prefix = generate_api_key()
    expires_at = None
    if body.expires_in_days:
        from datetime import timedelta

        expires_at = datetime.now(UTC) + timedelta(days=body.expires_in_days)
    key = ApiKey(
        user_id=user.id,
        name=body.name.strip(),
        prefix=prefix,
        key_hash=key_hash,
        scopes=["read"],
        rate_limit_per_minute=body.rate_limit_per_minute,
        expires_at=expires_at,
    )
    db.add(key)
    db.flush()
    write_audit(db, "apikey.create", user=user, detail={"key_id": str(key.id), "name": key.name})
    db.commit()
    return ok({
        "id": str(key.id),
        "name": key.name,
        "prefix": prefix,
        "api_key": plain,  # 仅此一次返回完整 Key
        "rate_limit_per_minute": key.rate_limit_per_minute,
        "expires_at": key.expires_at.isoformat() if key.expires_at else None,
        "created_at": key.created_at.isoformat() if key.created_at else None,
    }, "API Key 已创建，请立即保存完整 Key（仅显示一次）")


@router.get("")
def list_keys(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    keys = db.scalars(
        select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())
    ).all()
    return ok({"total": len(keys), "items": [_mask(k) for k in keys]})


@router.patch("/{key_id}")
def update_key(
    key_id: uuid.UUID,
    body: UpdateKeyRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    key = db.get(ApiKey, key_id)
    if key is None or key.user_id != user.id:
        raise BizError(CODE_NOT_FOUND, "Key 不存在")
    if body.name is not None:
        key.name = body.name.strip()
    if body.rate_limit_per_minute is not None:
        key.rate_limit_per_minute = body.rate_limit_per_minute
    db.add(key)
    write_audit(db, "apikey.update", user=user, detail={"key_id": str(key.id)})
    db.commit()
    return ok(_mask(key), "已更新")


@router.delete("/{key_id}")
def revoke_key(
    key_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    key = db.get(ApiKey, key_id)
    if key is None or key.user_id != user.id:
        raise BizError(CODE_NOT_FOUND, "Key 不存在")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)
        db.add(key)
        write_audit(db, "apikey.revoke", user=user, detail={"key_id": str(key.id)})
        db.commit()
    return ok(_mask(key), "已吊销")
