"""llm_judgements 表：LLM 判定留痕（T2.17，详细设计 3.2 关键不变量③）。

每次 LLM 判定（议题命名/分类/摘要、首发表述判定、事件终审）
记录模型名 + prompt_version + 输入/输出快照 + 成败 + 耗时，支持：
- 前端「该判定由哪个模型/哪版 prompt 产出」可标注可否决；
- 换 prompt 后对历史判定批量重跑对比（rerun 行以 input_payload.rerun_of 关联基线）。
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base


class LLMJudgement(Base):
    __tablename__ = "llm_judgements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("topics.id")
    )  # 议题相关判定；其他模块（如事件终审）判定可为空
    task_type: Mapped[str] = mapped_column(String(30), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    input_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)  # 代表标题/top 词等输入快照
    output_payload: Mapped[dict | None] = mapped_column(JSONB)  # 结构化输出；失败为 NULL
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    naming_method: Mapped[str | None] = mapped_column(String(20))  # llm / ctfidf_fallback
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "task_type IN ('topic_naming','topic_category','topic_summary','first_utterance','final_review','merge_confirm','reestimate_confirm','alert_summary','translate')",
            name="ck_llm_judgements_task_type",
        ),
    )
