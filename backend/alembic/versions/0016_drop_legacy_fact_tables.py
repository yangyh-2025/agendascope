"""0016_drop_legacy_fact_tables：数据库结构化重构（v3.0）—— DROP 旧事实表

业务背景：数据库从"应用数据库"升级为"单一事实源"。本次完全重构
articles/topics/agenda_events/persons_orgs/entity_relations 等事实表。

**保留**（用户配置/审计/流水）：
  users / api_keys / audit_logs / system_state / alert_rules / alerts /
  subscriptions / subscription_deliveries / collection_jobs / sources / llm_judgements

**DROP（即将重建）**：
  articles, topic_articles, topics, agenda_snapshots,
  agenda_events, agenda_event_evidence,
  persons_orgs, entity_relations, relation_evidences

注意：本迁移仅 DROP，新表在 0017-0020 中 CREATE。
"""
from alembic import op

revision = "0016_drop_legacy_fact_tables"
down_revision = "0015_watchlist"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 按外键依赖顺序 DROP（子表先，父表后）
    op.execute("DROP TABLE IF EXISTS relation_evidences CASCADE")
    op.execute("DROP TABLE IF EXISTS entity_relations CASCADE")
    op.execute("DROP TABLE IF EXISTS agenda_event_evidence CASCADE")
    op.execute("DROP TABLE IF EXISTS agenda_events CASCADE")
    op.execute("DROP TABLE IF EXISTS topic_articles CASCADE")
    op.execute("DROP TABLE IF EXISTS agenda_snapshots CASCADE")
    op.execute("DROP TABLE IF EXISTS topics CASCADE")
    op.execute("DROP TABLE IF EXISTS persons_orgs CASCADE")
    op.execute("DROP TABLE IF EXISTS articles CASCADE")


def downgrade() -> None:
    # 回滚时仅重建占位空表（结构由后续迁移决定），数据丢失不可恢复
    # 实际使用中请勿 downgrade 此迁移
    pass
