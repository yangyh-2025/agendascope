"""0012_alert_tasks_task_type：llm_judgements.task_type 扩展 'alert_summary' + 'translate'

阶段四两个 LLM 功能的判定结果同样走 llm_judgements 留痕
（模型名 + prompt_version + 输入/输出快照 + 成败 + 耗时）：
  - 'alert_summary'：预警规则命中触发时的告警理由中文摘要（app/alerting/alert_summary.py）
  - 'translate'：订阅日报/周报摘要的 LLM 翻译（app/alerting/llm_translate.py，替代 argos）

0011 迁移后的 CHECK 只允许 ('topic_naming','topic_category','topic_summary',
'first_utterance','final_review','merge_confirm','report_narrative','reestimate_confirm')，
本迁移删除旧约束并新增含 'alert_summary' 与 'translate' 的 CHECK。
"""
from alembic import op

revision = "0012_alert_tasks_task_type"
down_revision = "0011_report_narrative_task_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 与 0010/0011 相同的防御式写法：两个可能的约束名都先 DROP，避免环境差异导致失败
    op.execute("ALTER TABLE llm_judgements DROP CONSTRAINT IF EXISTS llm_judgements_task_type_check")
    op.execute("ALTER TABLE llm_judgements DROP CONSTRAINT IF EXISTS ck_llm_judgements_task_type")
    op.execute("""
    ALTER TABLE llm_judgements
    ADD CONSTRAINT ck_llm_judgements_task_type
    CHECK (task_type IN ('topic_naming','topic_category','topic_summary','first_utterance','final_review','merge_confirm','report_narrative','reestimate_confirm','alert_summary','translate'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE llm_judgements DROP CONSTRAINT IF EXISTS ck_llm_judgements_task_type")
    op.execute("""
    ALTER TABLE llm_judgements
    ADD CONSTRAINT ck_llm_judgements_task_type
    CHECK (task_type IN ('topic_naming','topic_category','topic_summary','first_utterance','final_review','merge_confirm','report_narrative','reestimate_confirm'))
    """)
