"""0005_final_review_check：llm_judgements.task_type 扩展 'final_review'（T3.12）

LLM 终审审查官（详细设计 4.2 算法 4 llm_final_review）的判定结果同样走
llm_judgements 留痕（模型名 + prompt_version + 输入/输出快照 + 成败 + 耗时）。
0004 迁移后的 CHECK 只允许
('topic_naming','topic_category','topic_summary','first_utterance')，
本迁移删除旧约束并新增含 'final_review' 的 CHECK。
"""
from alembic import op

revision = "0005_final_review_check"
down_revision = "0004_first_utterance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 与 0004 相同的防御式写法：两个可能的约束名都先 DROP，避免环境差异导致失败
    op.execute("ALTER TABLE llm_judgements DROP CONSTRAINT IF EXISTS llm_judgements_task_type_check")
    op.execute("ALTER TABLE llm_judgements DROP CONSTRAINT IF EXISTS ck_llm_judgements_task_type")
    op.execute("""
    ALTER TABLE llm_judgements
    ADD CONSTRAINT ck_llm_judgements_task_type
    CHECK (task_type IN ('topic_naming','topic_category','topic_summary','first_utterance','final_review'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE llm_judgements DROP CONSTRAINT IF EXISTS ck_llm_judgements_task_type")
    op.execute("""
    ALTER TABLE llm_judgements
    ADD CONSTRAINT ck_llm_judgements_task_type
    CHECK (task_type IN ('topic_naming','topic_category','topic_summary','first_utterance'))
    """)
