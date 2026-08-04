"""0018_create_topics_layer：L2 议题层 —— topics + 拆出的维度表

topics 主表精简：只保留核心字段（name/summary/centroid/status/lifecycle_state/confidence/
first_seen_at/last_seen_at）；keywords / country_scope / revision_log / no_merge_with
全部拆到独立维度表（可索引/可查询）。
"""
from alembic import op

revision = "0018_create_topics_layer"
down_revision = "0017_create_processing_layer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- topics 主表（含过渡期旧 JSONB 字段，向后兼容；新 API 不读） ----
    op.execute("""
    CREATE TABLE topics (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      name VARCHAR(300) NOT NULL,
      name_auto VARCHAR(300) NOT NULL,
      name_zh VARCHAR(300),
      topic_category VARCHAR(50),
      summary_zh TEXT,
      naming_method VARCHAR(20) NOT NULL DEFAULT 'llm',
      llm_model VARCHAR(100),
      prompt_version VARCHAR(50),
      cluster_method VARCHAR(20) NOT NULL DEFAULT 'agglomerative',
      centroid VECTOR(1024),
      status VARCHAR(15) NOT NULL DEFAULT 'emerging',
      lifecycle_state VARCHAR(15) NOT NULL DEFAULT 'nascent',
      confidence VARCHAR(15) NOT NULL DEFAULT 'watching',
      merged_into UUID REFERENCES topics(id),
      human_locked_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
      keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
      country_scope JSONB NOT NULL DEFAULT '[]'::jsonb,
      revision_log JSONB NOT NULL DEFAULT '[]'::jsonb,
      no_merge_with JSONB NOT NULL DEFAULT '[]'::jsonb,
      first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT ck_topics_naming_method CHECK (naming_method IN ('llm','ctfidf_fallback','keyword_fallback')),
      CONSTRAINT ck_topics_cluster_method CHECK (cluster_method IN ('bertopic','agglomerative','keyword_fallback')),
      CONSTRAINT ck_topics_status CHECK (status IN ('emerging','heating','stable','declining','archived')),
      CONSTRAINT ck_topics_lifecycle CHECK (lifecycle_state IN ('nascent','forming','confirmed','evolving','archived')),
      CONSTRAINT ck_topics_confidence CHECK (confidence IN ('watching','suspected','confirmed'))
    )
    """)
    op.execute("CREATE INDEX idx_topics_status ON topics(status)")
    op.execute("CREATE INDEX idx_topics_lifecycle ON topics(lifecycle_state)")
    op.execute("CREATE INDEX idx_topics_last_seen ON topics(last_seen_at DESC)")
    op.execute("CREATE INDEX idx_topics_category ON topics(topic_category)")
    op.execute("CREATE INDEX idx_topics_merged_into ON topics(merged_into) WHERE merged_into IS NOT NULL")

    # ---- topic_keywords（每议题的关键词，按 rank 排序） ----
    op.execute("""
    CREATE TABLE topic_keywords (
      topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
      keyword VARCHAR(100) NOT NULL,
      weight NUMERIC(5,4) NOT NULL DEFAULT 1.0,
      rank INTEGER NOT NULL,
      source VARCHAR(20) NOT NULL DEFAULT 'ctfidf',
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY (topic_id, rank),
      CONSTRAINT ck_tk_source CHECK (source IN ('ctfidf','llm','manual','imported'))
    )
    """)
    op.execute("CREATE INDEX idx_tk_keyword ON topic_keywords(keyword)")

    # ---- topic_countries（议题涉及国家） ----
    op.execute("""
    CREATE TABLE topic_countries (
      topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
      country_code CHAR(2) NOT NULL,
      article_count INTEGER NOT NULL DEFAULT 0,
      first_seen_at TIMESTAMPTZ NOT NULL,
      last_seen_at TIMESTAMPTZ NOT NULL,
      salience_peak NUMERIC(10,4),
      PRIMARY KEY (topic_id, country_code)
    )
    """)
    op.execute("CREATE INDEX idx_tc_country ON topic_countries(country_code)")

    # ---- topic_lifecycle_events（议题生命周期变化历史） ----
    op.execute("""
    CREATE TABLE topic_lifecycle_events (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
      event_type VARCHAR(30) NOT NULL,
      from_value JSONB,
      to_value JSONB,
      actor VARCHAR(20) NOT NULL,
      actor_id UUID,
      reason TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT ck_tle_event_type CHECK (event_type IN ('created','lifecycle_change','status_change','merged','split','renamed','origin_revised','locked','unlocked')),
      CONSTRAINT ck_tle_actor CHECK (actor IN ('system','nlp_worker','cluster_worker','detection_worker','relation_worker','snapshot_worker','llm','human'))
    )
    """)
    op.execute("CREATE INDEX idx_tle_topic ON topic_lifecycle_events(topic_id, created_at DESC)")
    op.execute("CREATE INDEX idx_tle_type ON topic_lifecycle_events(event_type)")

    # ---- topic_no_merge_pairs（不可归并议题对，双向） ----
    op.execute("""
    CREATE TABLE topic_no_merge_pairs (
      topic_id_a UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
      topic_id_b UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
      reason TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY (topic_id_a, topic_id_b),
      CONSTRAINT ck_tnmp_different CHECK (topic_id_a <> topic_id_b)
    )
    """)

    # ---- topic_articles（议题-文章关联） ----
    op.execute("""
    CREATE TABLE topic_articles (
      topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
      article_id UUID NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
      weight NUMERIC(4,3) NOT NULL DEFAULT 1.0,
      assign_method VARCHAR(15) NOT NULL DEFAULT 'online',
      assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY (topic_id, article_id),
      CONSTRAINT ck_ta_assign_method CHECK (assign_method IN ('online','recluster','merge','manual'))
    )
    """)
    op.execute("CREATE INDEX idx_ta_article ON topic_articles(article_id)")
    op.execute("CREATE INDEX idx_ta_assigned_at ON topic_articles(assigned_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS topic_articles CASCADE")
    op.execute("DROP TABLE IF EXISTS topic_no_merge_pairs CASCADE")
    op.execute("DROP TABLE IF EXISTS topic_lifecycle_events CASCADE")
    op.execute("DROP TABLE IF EXISTS topic_countries CASCADE")
    op.execute("DROP TABLE IF EXISTS topic_keywords CASCADE")
    op.execute("DROP TABLE IF EXISTS topics CASCADE")
