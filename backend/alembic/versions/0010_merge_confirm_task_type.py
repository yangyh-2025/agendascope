"""0010_merge_confirm_task_type：llm_judgements.task_type 扩展 'merge_confirm'（T3.3 增强）

议题归并 LLM 语义确认（merge_confirm）的判定结果同样走 llm_judgements 留痕
（模型名 + prompt_version + 输入/输出快照 + 成败 + 耗时）。0005 后的 CHECK 只
允许 ('topic_naming','topic_category','topic_summary','first_utterance',
'final_review')，本迁移删除旧约束并新增含 'merge_confirm' 的 CHECK。
"""
from alembic import op

revision = "0010_merge_confirm_task_type"
down_revision = "0009_embedding_dim_1024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 与 0005 相同的防御式写法：两个可能的约束名都先 DROP，避免环境差异导致失败
    op.execute("ALTER TABLE llm_judgements DROP CONSTRAINT IF EXISTS llm_judgements_task_type_check")
    op.execute("ALTER TABLE llm_judgements DROP CONSTRAINT IF EXISTS ck_llm_judgements_task_type")
    op.execute("""
    ALTER TABLE llm_judgements
    ADD CONSTRAINT ck_llm_judgements_task_type
    CHECK (task_type IN ('topic_naming','topic_category','topic_summary','first_utterance','final_review','merge_confirm'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE llm_judgements DROP CONSTRAINT IF EXISTS ck_llm_judgements_task_type")
    op.execute("""
    ALTER TABLE llm_judgements
    ADD CONSTRAINT ck_llm_judgements_task_type
    CHECK (task_type IN ('topic_naming','topic_category','topic_summary','first_utterance','final_review'))
    """)
