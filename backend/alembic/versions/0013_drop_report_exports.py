"""0013_drop_report_exports：删除报告板块（前端/后端/表全部下线）

业务背景：报告中心（议题深度报告/跨国对比简报/周期监测周报）从产品中下线。
本迁移做两件事：
  1) DROP TABLE report_exports（含其上所有索引/约束）
  2) llm_judgements.task_type CHECK 白名单去掉 'report_narrative'
     （保留 'topic_naming','topic_category','topic_summary','first_utterance',
            'final_review','merge_confirm','reestimate_confirm','alert_summary','translate'）

同时清理 alerts/alert_rules 中历史报告导出通知规则（名为 "系统-报告导出通知"）。
"""
from alembic import op

revision = "0013_drop_report_exports"
down_revision = "0012_alert_tasks_task_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 删除历史报告导出通知规则（alert_rules.name 匹配即删）
    op.execute("DELETE FROM alert_rules WHERE name = '系统-报告导出通知'")

    # 2) DROP report_exports 表（CASCADE 兼容外键引用）
    op.execute("DROP TABLE IF EXISTS report_exports CASCADE")

    # 3) llm_judgements.task_type CHECK 白名单去掉 'report_narrative'
    op.execute("ALTER TABLE llm_judgements DROP CONSTRAINT IF EXISTS llm_judgements_task_type_check")
    op.execute("ALTER TABLE llm_judgements DROP CONSTRAINT IF EXISTS ck_llm_judgements_task_type")
    op.execute("""
    ALTER TABLE llm_judgements
    ADD CONSTRAINT ck_llm_judgements_task_type
    CHECK (task_type IN ('topic_naming','topic_category','topic_summary','first_utterance','final_review','merge_confirm','reestimate_confirm','alert_summary','translate'))
    """)


def downgrade() -> None:
    # 回滚：恢复 CHECK 含 'report_narrative'；report_exports 表结构由 0001 迁移决定，此处不回建
    op.execute("ALTER TABLE llm_judgements DROP CONSTRAINT IF EXISTS ck_llm_judgements_task_type")
    op.execute("""
    ALTER TABLE llm_judgements
    ADD CONSTRAINT ck_llm_judgements_task_type
    CHECK (task_type IN ('topic_naming','topic_category','topic_summary','first_utterance','final_review','merge_confirm','report_narrative','reestimate_confirm','alert_summary','translate'))
    """)
