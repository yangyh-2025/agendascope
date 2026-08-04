"""0019_create_events_layer：L2 事件层 —— agenda_events + 关联表

agenda_events 主表：议程设置事件（首发源 → 跟随源 → 跨境传播）。
拆 follower_sequence JSONB 到 agenda_event_followers 显式表（含时序）。
新增 agenda_event_entities（事件-实体 N-N 关联，role 区分主客体）。
"""
from alembic import op

revision = "0019_create_events_layer"
down_revision = "0018_create_topics_layer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- agenda_events 主表 ----
    op.execute("""
    CREATE TABLE agenda_events (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      topic_id UUID NOT NULL REFERENCES topics(id),
      round_no INTEGER NOT NULL DEFAULT 1,
      status VARCHAR(12) NOT NULL DEFAULT 'watching',
      confidence VARCHAR(12) NOT NULL DEFAULT 'watching',
      origin_type VARCHAR(10) NOT NULL,
      origin_country_code CHAR(2) NOT NULL,
      origin_source_id UUID REFERENCES sources(id),
      origin_entity_id UUID REFERENCES persons_orgs(id),
      origin_at TIMESTAMPTZ NOT NULL,
      origin_confidence VARCHAR(10) NOT NULL DEFAULT 'medium',
      origin_quote TEXT,
      subject_entity_id UUID REFERENCES persons_orgs(id),
      object_entity_id UUID REFERENCES persons_orgs(id),
      follower_sequence JSONB NOT NULL DEFAULT '[]'::jsonb,
      stats_evidence JSONB,
      revision_log JSONB NOT NULL DEFAULT '[]'::jsonb,
      detection_method VARCHAR(20) NOT NULL DEFAULT 'llm',
      final_review JSONB,
      human_locked_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
      confirmed_by UUID REFERENCES users(id),
      confirmed_at TIMESTAMPTZ,
      dismiss_reason TEXT,
      is_false_positive BOOLEAN,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT ck_events_status CHECK (status IN ('watching','suspected','confirmed','dismissed','revised','archived')),
      CONSTRAINT ck_events_confidence CHECK (confidence IN ('watching','suspected','confirmed')),
      CONSTRAINT ck_events_origin_type CHECK (origin_type IN ('media','person','org')),
      CONSTRAINT ck_events_origin_confidence CHECK (origin_confidence IN ('high','medium','low')),
      CONSTRAINT ck_events_detection_method CHECK (detection_method IN ('llm','media_time_fallback'))
    )
    """)
    op.execute("CREATE INDEX idx_events_topic ON agenda_events(topic_id)")
    op.execute("CREATE INDEX idx_events_status ON agenda_events(status)")
    op.execute("CREATE INDEX idx_events_origin_country ON agenda_events(origin_country_code)")
    op.execute("CREATE INDEX idx_events_origin_at ON agenda_events(origin_at DESC)")
    op.execute("CREATE INDEX idx_events_subject ON agenda_events(subject_entity_id) WHERE subject_entity_id IS NOT NULL")
    op.execute("CREATE INDEX idx_events_object ON agenda_events(object_entity_id) WHERE object_entity_id IS NOT NULL")

    # ---- agenda_event_entities（事件-实体 N-N 关联） ----
    op.execute("""
    CREATE TABLE agenda_event_entities (
      event_id UUID NOT NULL REFERENCES agenda_events(id) ON DELETE CASCADE,
      entity_id UUID NOT NULL REFERENCES persons_orgs(id) ON DELETE CASCADE,
      role VARCHAR(20) NOT NULL,
      salience NUMERIC(4,3),
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY (event_id, entity_id, role),
      CONSTRAINT ck_aee_role CHECK (role IN ('subject','object','participant','mentioned'))
    )
    """)
    op.execute("CREATE INDEX idx_aee_entity ON agenda_event_entities(entity_id)")

    # ---- agenda_event_followers（事件传播链——显式时序） ----
    op.execute("""
    CREATE TABLE agenda_event_followers (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      event_id UUID NOT NULL REFERENCES agenda_events(id) ON DELETE CASCADE,
      source_id UUID NOT NULL REFERENCES sources(id),
      article_id UUID REFERENCES articles(id) ON DELETE SET NULL,
      country_code CHAR(2) NOT NULL,
      followed_at TIMESTAMPTZ NOT NULL,
      lag_seconds INTEGER,
      sequence_no INTEGER NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT ck_aef_lag_nonneg CHECK (lag_seconds IS NULL OR lag_seconds >= 0),
      CONSTRAINT uq_aef_event_source UNIQUE (event_id, source_id, followed_at)
    )
    """)
    op.execute("CREATE INDEX idx_aef_event ON agenda_event_followers(event_id, followed_at)")
    op.execute("CREATE INDEX idx_aef_source ON agenda_event_followers(source_id)")
    op.execute("CREATE INDEX idx_aef_country ON agenda_event_followers(country_code)")

    # ---- agenda_event_evidence（事件证据：首发报道/首发表述/统计快照） ----
    op.execute("""
    CREATE TABLE agenda_event_evidence (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      event_id UUID NOT NULL REFERENCES agenda_events(id) ON DELETE CASCADE,
      evidence_type VARCHAR(20) NOT NULL,
      article_id UUID REFERENCES articles(id),
      entity_id UUID REFERENCES persons_orgs(id),
      quote TEXT,
      occurred_at TIMESTAMPTZ NOT NULL,
      country_code CHAR(2),
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT ck_evidence_type CHECK (evidence_type IN ('origin_article','origin_utterance','follower_article','stat_snapshot'))
    )
    """)
    op.execute("CREATE INDEX idx_aeev_event ON agenda_event_evidence(event_id)")
    op.execute("CREATE INDEX idx_aeev_article ON agenda_event_evidence(article_id) WHERE article_id IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agenda_event_evidence CASCADE")
    op.execute("DROP TABLE IF EXISTS agenda_event_followers CASCADE")
    op.execute("DROP TABLE IF EXISTS agenda_event_entities CASCADE")
    op.execute("DROP TABLE IF EXISTS agenda_events CASCADE")
