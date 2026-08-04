"""0017_create_processing_layer：L1 加工层 —— article_processing / article_entities / worker_tasks

article_processing: 每篇文章的"处理流水账"——记录 NLP/聚类/实体抽取/关系抽取
各阶段的状态机。这是 GDELT 没有但我们加上的关键设计，支撑：
  - 用户可查询"还有多少文章待聚类"
  - 失败文章可单独重跑
  - 分布式 worker 用 FOR UPDATE SKIP LOCKED 领任务

article_entities: 实体-文章显式关联（替代 persons_orgs.first_utterances JSONB）

worker_tasks: 分布式 worker 任务队列（本次仅建表，未来启用）
"""
from alembic import op

revision = "0017_create_processing_layer"
down_revision = "0016_drop_legacy_fact_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- articles 表重建（L0 原始层） ----
    op.execute("""
    CREATE TABLE articles (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      source_id UUID NOT NULL REFERENCES sources(id),
      url VARCHAR(1000) NOT NULL,
      url_hash CHAR(64) NOT NULL,
      title TEXT NOT NULL,
      title_translated TEXT,
      content TEXT,
      summary TEXT,
      language VARCHAR(10) NOT NULL,
      language_confidence NUMERIC(4,3),
      published_at TIMESTAMPTZ NOT NULL,
      time_source VARCHAR(10) NOT NULL DEFAULT 'feed',
      crawled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      visible_at TIMESTAMPTZ,
      content_status VARCHAR(10) NOT NULL DEFAULT 'full',
      sentiment VARCHAR(10),
      sentiment_score NUMERIC(4,3),
      embedding VECTOR(1024),
      is_duplicate BOOLEAN NOT NULL DEFAULT false,
      canonical_id UUID REFERENCES articles(id),
      source_channel VARCHAR(10) NOT NULL DEFAULT 'rss',
      country_code CHAR(2) NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT ck_articles_time_source CHECK (time_source IN ('feed','crawled','gdelt')),
      CONSTRAINT ck_articles_content_status CHECK (content_status IN ('full','partial','failed')),
      CONSTRAINT ck_articles_sentiment CHECK (sentiment IN ('positive','neutral','negative')),
      CONSTRAINT ck_articles_sentiment_score CHECK (sentiment_score BETWEEN -1 AND 1),
      CONSTRAINT ck_articles_source_channel CHECK (source_channel IN ('rss','rsshub','gdelt'))
    )
    """)
    op.execute("CREATE UNIQUE INDEX idx_articles_url_hash ON articles(url_hash)")
    op.execute("CREATE INDEX idx_articles_published_at ON articles(published_at DESC)")
    op.execute("CREATE INDEX idx_articles_country_code ON articles(country_code)")
    op.execute("CREATE INDEX idx_articles_source_id ON articles(source_id)")
    op.execute("CREATE INDEX idx_articles_canonical_id ON articles(canonical_id) WHERE canonical_id IS NOT NULL")

    # ---- persons_orgs 表重建（L2 事实层——实体） ----
    op.execute("""
    CREATE TABLE persons_orgs (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      entity_type VARCHAR(15) NOT NULL,
      name VARCHAR(200) NOT NULL,
      name_zh VARCHAR(200),
      name_aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
      country_code CHAR(2) NOT NULL,
      role_title VARCHAR(200),
      monitored BOOLEAN NOT NULL DEFAULT true,
      is_seed BOOLEAN NOT NULL DEFAULT false,
      category VARCHAR(50),
      priority INTEGER NOT NULL DEFAULT 0,
      first_utterances JSONB NOT NULL DEFAULT '[]'::jsonb,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT ck_po_entity_type CHECK (entity_type IN ('person','thinktank','intl_org','gov_body'))
    )
    """)
    op.execute("CREATE INDEX idx_po_is_seed ON persons_orgs(is_seed)")
    op.execute("CREATE INDEX idx_po_category ON persons_orgs(category)")
    op.execute("CREATE INDEX idx_po_country ON persons_orgs(country_code)")
    op.execute("CREATE INDEX idx_po_name ON persons_orgs(name)")

    # ---- article_processing（L1 加工层核心） ----
    op.execute("""
    CREATE TABLE article_processing (
      article_id UUID PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
      nlp_status VARCHAR(12) NOT NULL DEFAULT 'pending',
      nlp_started_at TIMESTAMPTZ,
      nlp_finished_at TIMESTAMPTZ,
      nlp_error TEXT,
      cluster_status VARCHAR(12) NOT NULL DEFAULT 'pending',
      cluster_assigned_topic_id UUID,
      cluster_assigned_at TIMESTAMPTZ,
      cluster_similarity NUMERIC(4,3),
      entity_extract_status VARCHAR(12) NOT NULL DEFAULT 'pending',
      entity_extract_finished_at TIMESTAMPTZ,
      relation_extract_status VARCHAR(12) NOT NULL DEFAULT 'pending',
      relation_extract_finished_at TIMESTAMPTZ,
      translate_status VARCHAR(12) NOT NULL DEFAULT 'pending',
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT ck_ap_nlp CHECK (nlp_status IN ('pending','processing','done','failed','skipped')),
      CONSTRAINT ck_ap_cluster CHECK (cluster_status IN ('pending','processing','done','failed','skipped')),
      CONSTRAINT ck_ap_entity CHECK (entity_extract_status IN ('pending','processing','done','failed','skipped')),
      CONSTRAINT ck_ap_relation CHECK (relation_extract_status IN ('pending','processing','done','failed','skipped')),
      CONSTRAINT ck_ap_translate CHECK (translate_status IN ('pending','processing','done','failed','skipped','not_needed'))
    )
    """)
    op.execute("CREATE INDEX idx_ap_nlp_pending ON article_processing(nlp_status) WHERE nlp_status = 'pending'")
    op.execute("CREATE INDEX idx_ap_cluster_pending ON article_processing(cluster_status) WHERE cluster_status = 'pending'")
    op.execute("CREATE INDEX idx_ap_entity_pending ON article_processing(entity_extract_status) WHERE entity_extract_status = 'pending'")
    op.execute("CREATE INDEX idx_ap_relation_pending ON article_processing(relation_extract_status) WHERE relation_extract_status = 'pending'")

    # ---- article_entities（实体-文章显式关联） ----
    op.execute("""
    CREATE TABLE article_entities (
      article_id UUID NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
      entity_id UUID NOT NULL REFERENCES persons_orgs(id) ON DELETE CASCADE,
      mention_count INTEGER NOT NULL DEFAULT 1,
      first_offset INTEGER,
      sentiment_towards NUMERIC(4,3),
      is_primary_subject BOOLEAN NOT NULL DEFAULT false,
      extracted_by VARCHAR(20) NOT NULL,
      confidence NUMERIC(4,3) NOT NULL DEFAULT 0.5,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY (article_id, entity_id),
      CONSTRAINT ck_ae_extracted_by CHECK (extracted_by IN ('ner','llm','seed_match','manual')),
      CONSTRAINT ck_ae_confidence CHECK (confidence BETWEEN 0 AND 1)
    )
    """)
    op.execute("CREATE INDEX idx_ae_entity ON article_entities(entity_id)")
    op.execute("CREATE INDEX idx_ae_article ON article_entities(article_id)")
    op.execute("CREATE INDEX idx_ae_entity_primary ON article_entities(entity_id, is_primary_subject) WHERE is_primary_subject = true")

    # ---- worker_tasks（分布式 worker 任务队列） ----
    op.execute("""
    CREATE TABLE worker_tasks (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      task_type VARCHAR(30) NOT NULL,
      payload JSONB NOT NULL,
      priority INTEGER NOT NULL DEFAULT 0,
      status VARCHAR(15) NOT NULL DEFAULT 'pending',
      claimed_by VARCHAR(100),
      claimed_at TIMESTAMPTZ,
      finished_at TIMESTAMPTZ,
      attempts INTEGER NOT NULL DEFAULT 0,
      max_attempts INTEGER NOT NULL DEFAULT 3,
      result JSONB,
      error TEXT,
      expires_at TIMESTAMPTZ,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT ck_wt_task_type CHECK (task_type IN ('nlp_embed','cluster_assign','entity_extract','relation_extract','translate','collect','snapshot','detect')),
      CONSTRAINT ck_wt_status CHECK (status IN ('pending','claimed','done','failed','expired'))
    )
    """)
    op.execute("CREATE INDEX idx_wt_pending ON worker_tasks(task_type, priority DESC, created_at) WHERE status='pending'")
    op.execute("CREATE INDEX idx_wt_claimed ON worker_tasks(claimed_by, claimed_at) WHERE status='claimed'")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS worker_tasks CASCADE")
    op.execute("DROP TABLE IF EXISTS article_entities CASCADE")
    op.execute("DROP TABLE IF EXISTS article_processing CASCADE")
    op.execute("DROP TABLE IF EXISTS persons_orgs CASCADE")
    op.execute("DROP TABLE IF EXISTS articles CASCADE")
