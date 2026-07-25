"""topics 模块端点（详细设计 1.7 议题分裂/误并回滚）。"""
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agenda_engine.split import SplitError, split_topic
from app.api.deps import ROLE_AUTHORIZED, require_role
from app.core.errors import CODE_NOT_FOUND, CODE_STATE_INVALID, BizError, ok
from app.db.session import get_db
from app.models.user import User
from app.repositories.audit_repo import write_audit

router = APIRouter()


class TopicSplitRequest(BaseModel):
    """POST /topics/{parent_id}/split 请求体（详细设计 1.7）。"""

    child_topic_id: uuid.UUID = Field(description="待分裂出来的 child 议题 ID")


@router.post("/{parent_id}/split")
def split_topic_endpoint(
    parent_id: uuid.UUID,
    body: TopicSplitRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_AUTHORIZED)),
):
    """议题分裂/误并回滚（详细设计 1.7 + 4.2 算法 3 注释）。

    恢复 child 的 topic_id 与文章归属；双方写入 no_merge_with；
    双方 revision_log 追加 actor='human', trigger='manual_split'；
    关联 agenda_events 迁移回各自议题；写 audit_logs(action=topic.split)。
    """
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent", "")
    try:
        parent, child = split_topic(
            db, parent_id, body.child_topic_id, actor_user_id=user.id
        )
    except SplitError as exc:
        # 校验失败：审计 failure 后向上抛，由全局异常处理器转统一响应
        write_audit(
            db, "topic.split", user=user,
            resource=f"topics/{parent_id}",
            detail={"child_topic_id": str(body.child_topic_id), "error": exc.message},
            ip=ip, user_agent=ua, result="failure",
        )
        db.commit()
        raise
    except BizError:
        raise

    write_audit(
        db, "topic.split", user=user,
        resource=f"topics/{parent_id}",
        detail={
            "child_topic_id": str(body.child_topic_id),
            "restored_topic_id": str(child.id),
            "no_merge_pair": [str(parent.id), str(child.id)],
        },
        ip=ip, user_agent=ua,
    )
    db.commit()
    return ok({
        "parent_id": str(parent.id),
        "child_id": str(child.id),
        "restored_topic_id": str(child.id),
        "no_merge_pair": [str(parent.id), str(child.id)],
    })


# 显式 re-export，便于路由层静态分析
__all__ = ["router", "CODE_NOT_FOUND", "CODE_STATE_INVALID"]
