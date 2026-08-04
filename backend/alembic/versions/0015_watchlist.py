"""0015_watchlist：监控对象改造（50 精品 + 实体关系网络）

1) persons_orgs 表新增三个字段：
   - is_seed BOOLEAN NOT NULL DEFAULT false：是否种子 50 精品实体
   - category VARCHAR(50)：业务分类（"美国国安"/"欧盟外交" 等）
   - priority INTEGER NOT NULL DEFAULT 0：种子排序权重

2) 新建 entity_relations 表（监控对象关系边）：
   - 唯一约束 (subject_entity_id, object_entity_id, relation_type)
   - 关系类型封闭集合：meets/sanctions/appoints/criticizes/supports/opposes/
     allies_with/member_of/advises/funds/invests_in/signals_support/travelled_to/
     statement_about/family_of/other
   - confidence 含时间衰减；base_confidence 原始置信度
   - status: active/expired/rejected

3) 新建 relation_evidences 表（每条边的新闻证据）：
   - evidence_quote 必须是 article.content 的原文子串（程序校验）
   - 唯一约束 (relation_id, article_id)：同一条边同一篇文章只算一条证据
"""
from alembic import op

revision = "0015_watchlist"
down_revision = "0014_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) persons_orgs 加列
    op.execute("ALTER TABLE persons_orgs ADD COLUMN IF NOT EXISTS is_seed BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE persons_orgs ADD COLUMN IF NOT EXISTS category VARCHAR(50)")
    op.execute("ALTER TABLE persons_orgs ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 0")
    op.execute("CREATE INDEX IF NOT EXISTS idx_persons_orgs_is_seed ON persons_orgs(is_seed)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_persons_orgs_category ON persons_orgs(category)")

    # 2) entity_relations
    op.execute("""
    CREATE TABLE entity_relations (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      subject_entity_id UUID NOT NULL REFERENCES persons_orgs(id) ON DELETE CASCADE,
      object_entity_id UUID NOT NULL REFERENCES persons_orgs(id) ON DELETE CASCADE,
      relation_type VARCHAR(30) NOT NULL,
      confidence NUMERIC(4,3) NOT NULL DEFAULT 0.5,
      base_confidence NUMERIC(4,3) NOT NULL DEFAULT 0.5,
      first_seen_at TIMESTAMPTZ NOT NULL,
      last_seen_at TIMESTAMPTZ NOT NULL,
      evidence_count INTEGER NOT NULL DEFAULT 1,
      status VARCHAR(15) NOT NULL DEFAULT 'active',
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT uq_er_subject_object_type UNIQUE (subject_entity_id, object_entity_id, relation_type),
      CONSTRAINT ck_er_relation_type CHECK (relation_type IN (
        'meets','sanctions','appoints','criticizes','supports','opposes',
        'allies_with','member_of','advises','funds','invests_in','signals_support',
        'travelled_to','statement_about','family_of','other'
      )),
      CONSTRAINT ck_er_status CHECK (status IN ('active','expired','rejected')),
      CONSTRAINT ck_er_confidence CHECK (confidence BETWEEN 0 AND 1),
      CONSTRAINT ck_er_base_confidence CHECK (base_confidence BETWEEN 0 AND 1)
    )
    """)
    op.execute("CREATE INDEX idx_er_subject ON entity_relations(subject_entity_id)")
    op.execute("CREATE INDEX idx_er_object ON entity_relations(object_entity_id)")
    op.execute("CREATE INDEX idx_er_status ON entity_relations(status)")

    # 3) relation_evidences
    op.execute("""
    CREATE TABLE relation_evidences (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      relation_id UUID NOT NULL REFERENCES entity_relations(id) ON DELETE CASCADE,
      article_id UUID NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
      evidence_quote TEXT NOT NULL,
      evidence_quote_zh TEXT,
      context_paragraph TEXT,
      published_at TIMESTAMPTZ NOT NULL,
      extracted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      llm_judgement_id UUID REFERENCES llm_judgements(id),
      CONSTRAINT uq_evidence_relation_article UNIQUE (relation_id, article_id)
    )
    """)
    op.execute("CREATE INDEX idx_evidence_relation ON relation_evidences(relation_id)")
    op.execute("CREATE INDEX idx_evidence_article ON relation_evidences(article_id)")

    # 4) llm_judgements.task_type 白名单加 'relation_extract'
    op.execute("ALTER TABLE llm_judgements DROP CONSTRAINT IF EXISTS llm_judgements_task_type_check")
    op.execute("ALTER TABLE llm_judgements DROP CONSTRAINT IF EXISTS ck_llm_judgements_task_type")
    op.execute("""
    ALTER TABLE llm_judgements
    ADD CONSTRAINT ck_llm_judgements_task_type
    CHECK (task_type IN ('topic_naming','topic_category','topic_summary','first_utterance','final_review','merge_confirm','reestimate_confirm','alert_summary','translate','relation_extract'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE llm_judgements DROP CONSTRAINT IF EXISTS ck_llm_judgements_task_type")
    op.execute("""
    ALTER TABLE llm_judgements
    ADD CONSTRAINT ck_llm_judgements_task_type
    CHECK (task_type IN ('topic_naming','topic_category','topic_summary','first_utterance','final_review','merge_confirm','reestimate_confirm','alert_summary','translate'))
    """)
    op.execute("DROP TABLE IF EXISTS relation_evidences CASCADE")
    op.execute("DROP TABLE IF EXISTS entity_relations CASCADE")
    op.execute("ALTER TABLE persons_orgs DROP COLUMN IF EXISTS is_seed")
    op.execute("ALTER TABLE persons_orgs DROP COLUMN IF EXISTS category")
    op.execute("ALTER TABLE persons_orgs DROP COLUMN IF EXISTS priority")
