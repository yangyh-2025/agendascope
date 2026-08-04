"""0020_create_facts_layer：L2/L3 事实层 —— entity_relations + relation_evidences + 三类快照表

entity_relations: 实体-实体关系（带时间衰减置信度）
relation_evidences: 每条关系的新闻证据（evidence_quote 必须 article.content 原文子串）
topic_snapshots: 议题×国家×时间窗 显著性快照（继承旧 agenda_snapshots 结构）
entity_snapshots: 实体×时间窗 提及快照（新）
source_snapshots: 媒体源×时间窗 表现快照（新）
"""
from alembic import op

revision = "0020_create_facts_layer"
down_revision = "0019_create_events_layer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- entity_relations ----
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

    # ---- relation_evidences ----
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

    # ---- topic_snapshots（议题×国家×时间窗 显著性） ----
    op.execute("""
    CREATE TABLE topic_snapshots (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      country_code CHAR(2) NOT NULL,
      topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
      window_start TIMESTAMPTZ NOT NULL,
      window_end TIMESTAMPTZ NOT NULL,
      granularity VARCHAR(5) NOT NULL,
      article_count INTEGER NOT NULL DEFAULT 0,
      salience_score NUMERIC(10,4) NOT NULL DEFAULT 0,
      salience_rank INTEGER NOT NULL,
      sentiment_pos NUMERIC(5,4),
      sentiment_neu NUMERIC(5,4),
      sentiment_neg NUMERIC(5,4),
      top_attributes JSONB,
      network_metrics JSONB,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT ck_snap_window CHECK (window_end > window_start),
      CONSTRAINT ck_snap_granularity CHECK (granularity IN ('hour','day','week')),
      CONSTRAINT ck_snap_article_count CHECK (article_count >= 0),
      CONSTRAINT ck_snap_rank CHECK (salience_rank >= 1),
      CONSTRAINT uq_snap_scope UNIQUE (country_code, topic_id, window_start, granularity)
    )
    """)
    op.execute("CREATE INDEX idx_snap_topic ON topic_snapshots(topic_id, window_start DESC)")
    op.execute("CREATE INDEX idx_snap_country ON topic_snapshots(country_code, window_start DESC)")
    op.execute("CREATE INDEX idx_snap_window ON topic_snapshots(window_start DESC, granularity)")

    # ---- entity_snapshots（实体×时间窗 提及快照） ----
    op.execute("""
    CREATE TABLE entity_snapshots (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      entity_id UUID NOT NULL REFERENCES persons_orgs(id) ON DELETE CASCADE,
      window_start TIMESTAMPTZ NOT NULL,
      window_end TIMESTAMPTZ NOT NULL,
      granularity VARCHAR(5) NOT NULL,
      mention_count INTEGER NOT NULL DEFAULT 0,
      article_count INTEGER NOT NULL DEFAULT 0,
      unique_sources INTEGER NOT NULL DEFAULT 0,
      sentiment_avg NUMERIC(5,4),
      sentiment_pos NUMERIC(5,4),
      sentiment_neg NUMERIC(5,4),
      first_utterance_count INTEGER NOT NULL DEFAULT 0,
      relation_new_count INTEGER NOT NULL DEFAULT 0,
      top_topics JSONB,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT ck_es_window CHECK (window_end > window_start),
      CONSTRAINT ck_es_granularity CHECK (granularity IN ('hour','day','week')),
      CONSTRAINT uq_es_scope UNIQUE (entity_id, window_start, granularity)
    )
    """)
    op.execute("CREATE INDEX idx_es_entity ON entity_snapshots(entity_id, window_start DESC)")
    op.execute("CREATE INDEX idx_es_window ON entity_snapshots(window_start DESC, granularity)")

    # ---- source_snapshots（媒体源×时间窗 表现快照） ----
    op.execute("""
    CREATE TABLE source_snapshots (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      source_id UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
      window_start TIMESTAMPTZ NOT NULL,
      window_end TIMESTAMPTZ NOT NULL,
      granularity VARCHAR(5) NOT NULL,
      articles_published INTEGER NOT NULL DEFAULT 0,
      articles_collected INTEGER NOT NULL DEFAULT 0,
      first_utterance_count INTEGER NOT NULL DEFAULT 0,
      follow_count INTEGER NOT NULL DEFAULT 0,
      avg_lag_seconds INTEGER,
      collection_success_rate NUMERIC(4,3),
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT ck_ss_window CHECK (window_end > window_start),
      CONSTRAINT ck_ss_granularity CHECK (granularity IN ('hour','day','week')),
      CONSTRAINT uq_ss_scope UNIQUE (source_id, window_start, granularity)
    )
    """)
    op.execute("CREATE INDEX idx_ss_source ON source_snapshots(source_id, window_start DESC)")
    op.execute("CREATE INDEX idx_ss_window ON source_snapshots(window_start DESC, granularity)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS source_snapshots CASCADE")
    op.execute("DROP TABLE IF EXISTS entity_snapshots CASCADE")
    op.execute("DROP TABLE IF EXISTS topic_snapshots CASCADE")
    op.execute("DROP TABLE IF EXISTS relation_evidences CASCADE")
    op.execute("DROP TABLE IF EXISTS entity_relations CASCADE")
