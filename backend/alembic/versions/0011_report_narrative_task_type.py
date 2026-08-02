"""0011_report_narrative_task_type：llm_judgements.task_type 扩展 'report_narrative' + 'reestimate_confirm'

阶段三两个 LLM 功能的判定结果同样走 llm_judgements 留痕
（模型名 + prompt_version + 输入/输出快照 + 成败 + 耗时）：
  - 'report_narrative'：议题深度报告概览的分析叙述（app/services/report_narrative.py）
  - 'reestimate_confirm'：增量重估的 LLM 复核（app/agenda_engine/revision.py，是否推翻首发判定）

0010 迁移后的 CHECK 只允许 ('topic_naming','topic_category','topic_summary',
'first_utterance','final_review','merge_confirm')，本迁移删除旧约束并新增
含 'report_narrative' 与 'reestimate_confirm' 的 CHECK。
"""
from alembic import op

revision = "0011_report_narrative_task_type"
down_revision = "0010_merge_confirm_task_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 与 0010 相同的防御式写法：两个可能的约束名都先 DROP，避免环境差异导致失败
    op.execute("ALTER TABLE llm_judgements DROP CONSTRAINT IF EXISTS llm_judgements_task_type_check")
    op.execute("ALTER TABLE llm_judgements DROP CONSTRAINT IF EXISTS ck_llm_judgements_task_type")
    op.execute("""
    ALTER TABLE llm_judgements
    ADD CONSTRAINT ck_llm_judgements_task_type
    CHECK (task_type IN ('topic_naming','topic_category','topic_summary','first_utterance','final_review','merge_confirm','report_narrative','reestimate_confirm'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE llm_judgements DROP CONSTRAINT IF EXISTS ck_llm_judgements_task_type")
    op.execute("""
    ALTER TABLE llm_judgements
    ADD CONSTRAINT ck_llm_judgements_task_type
    CHECK (task_type IN ('topic_naming','topic_category','topic_summary','first_utterance','final_review','merge_confirm'))
    """)
