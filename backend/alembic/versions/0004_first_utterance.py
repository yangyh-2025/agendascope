"""0004_first_utterance：llm_judgements.task_type 扩展 'first_utterance'（T3.8）

LLM 首发表述判定器（详细设计 4.2 算法 4 llm_first_utterance）的判定结果
也走 llm_judgements 留痕（模型名 + prompt_version + 输入/输出快照 + 成败 + 耗时）。
原 CHECK 约束只允许 ('topic_naming','topic_category','topic_summary')，
本迁移删除旧约束并新增含 'first_utterance' 的 CHECK。
"""
from alembic import op

revision = "0004_first_utterance"
down_revision = "0003_llm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 旧 CHECK 约束是 PostgreSQL 默认命名（llm_judgements_task_type_check），
    # 0003 迁移里只写了 CHECK 子句未命名；SQLAlchemy 模型端命名的 ck_* 是模型层别名
    op.execute("ALTER TABLE llm_judgements DROP CONSTRAINT IF EXISTS llm_judgements_task_type_check")
    op.execute("ALTER TABLE llm_judgements DROP CONSTRAINT IF EXISTS ck_llm_judgements_task_type")
    op.execute("""
    ALTER TABLE llm_judgements
    ADD CONSTRAINT ck_llm_judgements_task_type
    CHECK (task_type IN ('topic_naming','topic_category','topic_summary','first_utterance'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE llm_judgements DROP CONSTRAINT IF EXISTS ck_llm_judgements_task_type")
    op.execute("""
    ALTER TABLE llm_judgements
    ADD CONSTRAINT ck_llm_judgements_task_type
    CHECK (task_type IN ('topic_naming','topic_category','topic_summary'))
    """)
