"""初始迁移：14 张核心表（DDL 严格按 2-详细设计.md 第二章）

users / sources / collection_jobs / articles / topics / topic_articles /
agenda_snapshots / agenda_events / agenda_event_evidence / persons_orgs /
alert_rules / alerts / report_exports / audit_logs

外键依赖拓扑序（详细设计 2.17）：
users / sources / topics / persons_orgs
  → articles(FK→sources, 自引用 canonical_id)
  → collection_jobs / topic_articles / agenda_snapshots / agenda_events
  → agenda_event_evidence / alert_rules
  → alerts / report_exports / audit_logs

Revision ID: 0001_init
"""
from alembic import op

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- 必备扩展（详细设计 2.2） ----
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ---- users（2.3） ----
    op.execute("""
    CREATE TABLE users (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        username        VARCHAR(64)  NOT NULL,
        password_hash   VARCHAR(255) NOT NULL,
        display_name    VARCHAR(100) NOT NULL,
        email           VARCHAR(255),
        role            VARCHAR(20)  NOT NULL DEFAULT 'registered'
                        CHECK (role IN ('registered','authorized','admin')),
        status          VARCHAR(20)  NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','disabled')),
        locale          VARCHAR(10)  NOT NULL DEFAULT 'zh-CN',
        timezone        VARCHAR(50)  NOT NULL DEFAULT 'UTC',
        totp_secret     VARCHAR(64),
        must_change_password BOOLEAN NOT NULL DEFAULT TRUE,
        failed_login_count INT NOT NULL DEFAULT 0,
        locked_until    TIMESTAMPTZ,
        last_login_at   TIMESTAMPTZ,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("CREATE UNIQUE INDEX idx_users_username ON users(username) WHERE status != 'disabled'")
    op.execute("CREATE INDEX idx_users_role ON users(role)")
    op.execute("COMMENT ON TABLE  users IS '用户表(私有化机构内账号; 游客无账号)'")
    op.execute("COMMENT ON COLUMN users.role IS '角色: registered=注册用户, authorized=授权用户, admin=管理员'")
    op.execute("COMMENT ON COLUMN users.must_change_password IS '管理员创建账号后首次登录强制改密(等保要求)'")
    op.execute("COMMENT ON COLUMN users.failed_login_count IS '连续登录失败计数, ≥5 锁定 15 分钟(等保要求)'")

    # ---- sources（2.4） ----
    op.execute("""
    CREATE TABLE sources (
        id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name                VARCHAR(200) NOT NULL,
        name_zh             VARCHAR(200),
        country_code        CHAR(2)      NOT NULL,
        homepage_url        VARCHAR(500) NOT NULL,
        feed_url            VARCHAR(500),
        collect_mode        VARCHAR(10)  NOT NULL DEFAULT 'rss'
                            CHECK (collect_mode IN ('rss','rsshub','gdelt')),
        adapter_type        VARCHAR(10)  NOT NULL DEFAULT 'rss'
                            CHECK (adapter_type IN ('rss','pipeline')),
        crawl_config        JSONB        NOT NULL DEFAULT '{}',
        media_type          VARCHAR(20)  NOT NULL
                            CHECK (media_type IN ('newspaper','agency','broadcast','online')),
        language            VARCHAR(10)  NOT NULL,
        poll_interval_min   SMALLINT     NOT NULL DEFAULT 5 CHECK (poll_interval_min BETWEEN 1 AND 60),
        audience_weight     NUMERIC(5,2) CHECK (audience_weight BETWEEN 0 AND 100),
        coverage_confidence VARCHAR(10)  NOT NULL DEFAULT 'medium'
                            CHECK (coverage_confidence IN ('high','medium','low')),
        status              VARCHAR(10)  NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active','degraded','failed')),
        is_custom           BOOLEAN      NOT NULL DEFAULT FALSE,
        consecutive_failures INT         NOT NULL DEFAULT 0,
        degraded_since      TIMESTAMPTZ,
        last_success_at     TIMESTAMPTZ,
        status_history      JSONB        NOT NULL DEFAULT '[]',
        created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("CREATE UNIQUE INDEX idx_sources_feed_url ON sources(feed_url) WHERE feed_url IS NOT NULL")
    op.execute("CREATE INDEX idx_sources_country ON sources(country_code, status)")
    op.execute("CREATE INDEX idx_sources_status ON sources(status) WHERE status != 'active'")
    op.execute("COMMENT ON TABLE  sources IS '媒体源目录(30 国, ~600 源, 人工维护低速增长)'")
    op.execute("COMMENT ON COLUMN sources.status IS '健康状态机: active→(连续3次失败)→degraded→(>24h)→failed; degraded 自动切 RSSHub/GDELT 兜底'")
    op.execute("COMMENT ON COLUMN sources.adapter_type IS '三层采集架构的适配器路由(ADR-011): rss 源由通用 RSS 采集器(feedparser+trafilatura+治理状态机)承载; pipeline 源由配置驱动爬虫管线 Fetcher→Discoverer→Extractor 承载'")
    op.execute("COMMENT ON COLUMN sources.crawl_config IS '配置即爬虫: {fetcher:{type,params}, discoverer:{type:rss|sitemap|list_page}, extractor:{type:trafilatura|readability|generic_css}, entry_points:[], scroll_pages, post_extra_action, proxy:global_site_proxy|cn_site_proxy(国内外代理分级, 参考 IIS)}; 管理界面保存即生效(DB 配置+Pub/Sub 重载信号), 种子参数可复用 IIS 15 媒体任务(Apache-2.0)'")
    op.execute("COMMENT ON COLUMN sources.audience_weight IS '受众份额估算 0-100; 每国 active 源合计 ≥70 视为覆盖率达标'")
    op.execute("COMMENT ON COLUMN sources.coverage_confidence IS '覆盖率置信度: 五源交集命中≥4→high, 2-3→medium, ≤1→low'")

    # ---- topics（2.7） ----
    op.execute("""
    CREATE TABLE topics (
        id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name             VARCHAR(300) NOT NULL,
        name_auto        VARCHAR(300) NOT NULL,
        name_zh          VARCHAR(300),
        topic_category   VARCHAR(50),
        summary_zh       TEXT,
        naming_method    VARCHAR(20) NOT NULL DEFAULT 'llm'
                         CHECK (naming_method IN ('llm','ctfidf_fallback','keyword_fallback')),
        keywords         JSONB        NOT NULL DEFAULT '[]',
        cluster_method   VARCHAR(20) NOT NULL DEFAULT 'bertopic'
                         CHECK (cluster_method IN ('bertopic','agglomerative','keyword_fallback')),
        centroid         vector(768),
        country_scope    JSONB        NOT NULL DEFAULT '[]',
        status           VARCHAR(15) NOT NULL DEFAULT 'emerging'
                         CHECK (status IN ('emerging','heating','stable','declining','archived')),
        lifecycle_state  VARCHAR(15) NOT NULL DEFAULT 'nascent'
                         CHECK (lifecycle_state IN ('nascent','forming','confirmed','evolving','archived')),
        confidence       VARCHAR(15) NOT NULL DEFAULT 'watching'
                         CHECK (confidence IN ('watching','suspected','confirmed')),
        merged_into      UUID         REFERENCES topics(id),
        no_merge_with    JSONB        NOT NULL DEFAULT '[]',
        revision_log     JSONB        NOT NULL DEFAULT '[]',
        human_locked_fields JSONB     NOT NULL DEFAULT '[]',
        first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("CREATE INDEX idx_topics_lifecycle ON topics(lifecycle_state) WHERE lifecycle_state NOT IN ('archived')")
    op.execute("CREATE INDEX idx_topics_last_seen ON topics(last_seen_at DESC)")
    op.execute("CREATE INDEX idx_topics_merged_into ON topics(merged_into) WHERE merged_into IS NOT NULL")
    op.execute("CREATE INDEX idx_topics_country_scope ON topics USING gin(country_scope)")
    op.execute("CREATE INDEX idx_topics_centroid ON topics USING hnsw (centroid vector_cosine_ops) WITH (m = 16, ef_construction = 64)")
    op.execute("COMMENT ON TABLE  topics IS '议题(BERTopic+Agglomerative 聚类产出, LLM 命名/分类/归并; 含生命周期与自我纠错字段)'")
    op.execute("COMMENT ON COLUMN topics.lifecycle_state IS 'nascent=萌芽(孤证微簇size=1保留)/forming=形成中/confirmed=已确认/evolving=演化(合并/分裂)/archived=消亡(7天无新报道,估算)'")
    op.execute("COMMENT ON COLUMN topics.merged_into IS '次日自动归并: 跨语言向量比对 ≥0.85(估算)并入旧议题, topic_id 复用, 本行指向存活议题'")
    op.execute("COMMENT ON COLUMN topics.no_merge_with IS '误并拆分回滚后写入, 次日归并跳过该议题对, 防止再次误并'")
    op.execute("COMMENT ON COLUMN topics.centroid IS '议题向量中心: 时间衰减加权池化(非 mean pooling, IIS 教训), 供次日归并跨语言比对'")

    # ---- persons_orgs（2.12） ----
    op.execute("""
    CREATE TABLE persons_orgs (
        id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        entity_type      VARCHAR(15) NOT NULL
                         CHECK (entity_type IN ('person','thinktank','intl_org','gov_body')),
        name             VARCHAR(200) NOT NULL,
        name_zh          VARCHAR(200),
        name_aliases     JSONB NOT NULL DEFAULT '[]',
        country_code     CHAR(2) NOT NULL,
        role_title       VARCHAR(200),
        monitored        BOOLEAN NOT NULL DEFAULT TRUE,
        first_utterances JSONB NOT NULL DEFAULT '[]',
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("CREATE UNIQUE INDEX idx_po_name_country ON persons_orgs(name, country_code, entity_type)")
    op.execute("CREATE INDEX idx_po_country ON persons_orgs(country_code) WHERE monitored = TRUE")
    op.execute("CREATE INDEX idx_po_aliases ON persons_orgs USING gin(name_aliases)")
    op.execute("COMMENT ON TABLE persons_orgs IS '关键人物与机构实体库(官员/政治人物/智库/国际组织; 人物首发→媒体扩散链路的锚点, US-15)'")
    op.execute("COMMENT ON COLUMN persons_orgs.first_utterances IS '首发表述记录(LLM 判定\"首次提出\"并输出 evidence_quote; 升级为议程设置事件后回填 linked_event_id)'")

    # ---- articles（2.6） ----
    op.execute("""
    CREATE TABLE articles (
        id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        source_id        UUID         NOT NULL REFERENCES sources(id),
        url              VARCHAR(1000) NOT NULL,
        url_hash         CHAR(64)     NOT NULL,
        title            TEXT         NOT NULL,
        title_translated TEXT,
        content          TEXT,
        summary          TEXT,
        language         VARCHAR(10)  NOT NULL,
        language_confidence NUMERIC(4,3),
        published_at     TIMESTAMPTZ  NOT NULL,
        time_source      VARCHAR(10)  NOT NULL DEFAULT 'feed'
                         CHECK (time_source IN ('feed','crawled','gdelt')),
        crawled_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        visible_at       TIMESTAMPTZ,
        content_status   VARCHAR(10)  NOT NULL DEFAULT 'full'
                         CHECK (content_status IN ('full','partial','failed')),
        sentiment        VARCHAR(10)  CHECK (sentiment IN ('positive','neutral','negative')),
        sentiment_score  NUMERIC(4,3) CHECK (sentiment_score BETWEEN -1 AND 1),
        embedding        vector(768),
        is_duplicate     BOOLEAN      NOT NULL DEFAULT FALSE,
        canonical_id     UUID         REFERENCES articles(id),
        source_channel   VARCHAR(10)  NOT NULL DEFAULT 'rss'
                         CHECK (source_channel IN ('rss','rsshub','gdelt')),
        country_code     CHAR(2)      NOT NULL,
        created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("CREATE UNIQUE INDEX idx_articles_url_hash ON articles(url_hash)")
    op.execute("CREATE INDEX idx_articles_published ON articles(published_at DESC)")
    op.execute("CREATE INDEX idx_articles_source_time ON articles(source_id, published_at DESC)")
    op.execute("CREATE INDEX idx_articles_country_time ON articles(country_code, published_at DESC)")
    op.execute("CREATE INDEX idx_articles_canonical ON articles(canonical_id) WHERE is_duplicate = TRUE")
    op.execute("CREATE INDEX idx_articles_embedding ON articles USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)")
    op.execute("COMMENT ON TABLE  articles IS '新闻文章(结构化+正文; 全文检索副本同步 ES; 原始 HTML 90 天后清理)'")
    op.execute("COMMENT ON COLUMN articles.visible_at IS 'published_at→visible_at 即端到端延迟, 逐篇度量 P95≤30min/红线≤2h'")
    op.execute("COMMENT ON COLUMN articles.is_duplicate IS '标题向量相似度>0.92 判为转载, 合并为一组, canonical_id 指向首发主记录'")
    op.execute("COMMENT ON COLUMN articles.embedding IS '跨语言向量 768 维, pgvector 存储, HNSW 索引供在线增量归簇近邻检索'")

    # ---- collection_jobs（2.5） ----
    op.execute("""
    CREATE TABLE collection_jobs (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        source_id       UUID         NOT NULL REFERENCES sources(id),
        channel         VARCHAR(10)  NOT NULL
                        CHECK (channel IN ('rss','rsshub','gdelt')),
        scheduled_at    TIMESTAMPTZ  NOT NULL,
        started_at      TIMESTAMPTZ,
        finished_at     TIMESTAMPTZ,
        status          VARCHAR(15)  NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN ('PENDING','RUNNING','SUCCESS','TEMP_FAIL','PERM_FAIL','SKIPPED')),
        retry_count     INT          NOT NULL DEFAULT 0,
        next_run_at     TIMESTAMPTZ,
        http_status     SMALLINT,
        articles_found  INT          NOT NULL DEFAULT 0,
        articles_new    INT          NOT NULL DEFAULT 0,
        error           TEXT,
        latency_stats   JSONB,
        created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("CREATE INDEX idx_jobs_source_time ON collection_jobs(source_id, scheduled_at DESC)")
    op.execute("CREATE INDEX idx_jobs_status_time ON collection_jobs(status, COALESCE(next_run_at, scheduled_at)) WHERE status IN ('PENDING','RUNNING','TEMP_FAIL')")
    # 大表按月分区 PARTITION BY RANGE (scheduled_at) 与 90 天 detach 归档：详见详细设计 2.5/2.16，
    # Phase 1 单表承载，分区化随数据量增长在后续迁移执行（2.17 变更原则 3）。
    op.execute("COMMENT ON TABLE collection_jobs IS '采集任务调度与延迟度量(支撑 P95 延迟监控与源健康状态机)'")
    op.execute("COMMENT ON COLUMN collection_jobs.status IS '治理状态机(对齐 IIS, ADR-011): PENDING→RUNNING→SUCCESS / TEMP_FAIL(retry_count+1, next_run_at 退避重试, ≤3 次)→PERM_FAIL(不再调度, 计入源失败率告警); SKIPPED=should_crawl 裁决去重跳过(防重第一层)'")
    op.execute("COMMENT ON COLUMN collection_jobs.retry_count IS '配合 next_run_at 实现退避重试; should_crawl(url, max_retries=3) 是去重与重试的统一裁决点'")

    # ---- topic_articles（2.8） ----
    op.execute("""
    CREATE TABLE topic_articles (
        topic_id     UUID    NOT NULL REFERENCES topics(id),
        article_id   UUID    NOT NULL REFERENCES articles(id),
        weight       NUMERIC(4,3) NOT NULL DEFAULT 1.0,
        assign_method VARCHAR(15) NOT NULL DEFAULT 'online'
                      CHECK (assign_method IN ('online','recluster','merge','manual')),
        assigned_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (topic_id, article_id)
    )
    """)
    op.execute("CREATE INDEX idx_ta_article ON topic_articles(article_id)")
    op.execute("CREATE INDEX idx_ta_topic_time ON topic_articles(topic_id, assigned_at DESC)")
    op.execute("COMMENT ON TABLE topic_articles IS '议题-文章关联(含归属权重与归入方式; 议题合并/分裂时批量迁移并留 assign_method 痕迹)'")

    # ---- agenda_snapshots（2.9） ----
    op.execute("""
    CREATE TABLE agenda_snapshots (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        country_code    CHAR(2) NOT NULL,
        topic_id        UUID    NOT NULL REFERENCES topics(id),
        window_start    TIMESTAMPTZ NOT NULL,
        window_end      TIMESTAMPTZ NOT NULL CHECK (window_end > window_start),
        granularity     VARCHAR(5)  NOT NULL DEFAULT 'day'
                        CHECK (granularity IN ('hour','day','week')),
        article_count   INT     NOT NULL DEFAULT 0 CHECK (article_count >= 0),
        salience_score  NUMERIC(10,4) NOT NULL DEFAULT 0,
        salience_rank   INT     NOT NULL CHECK (salience_rank >= 1),
        sentiment_pos   NUMERIC(5,4),
        sentiment_neu   NUMERIC(5,4),
        sentiment_neg   NUMERIC(5,4),
        top_attributes  JSONB,
        network_metrics JSONB,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (country_code, topic_id, window_start, granularity)
    )
    """)
    op.execute("CREATE INDEX idx_snap_topic ON agenda_snapshots(topic_id, country_code, window_start DESC)")
    op.execute("CREATE INDEX idx_snap_country_time ON agenda_snapshots(country_code, window_start DESC, granularity)")
    op.execute("COMMENT ON TABLE agenda_snapshots IS '议程快照(国家×议题×时间窗显著性; 看板/时间线/预警评估的数据源; 15min 滚动刷新)'")

    # ---- agenda_events（2.10） ----
    op.execute("""
    CREATE TABLE agenda_events (
        id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        topic_id           UUID NOT NULL REFERENCES topics(id),
        round_no           INT  NOT NULL DEFAULT 1,
        status             VARCHAR(12) NOT NULL DEFAULT 'watching'
                           CHECK (status IN ('watching','suspected','confirmed','dismissed','revised','archived')),
        confidence         VARCHAR(12) NOT NULL DEFAULT 'watching'
                           CHECK (confidence IN ('watching','suspected','confirmed')),
        origin_type        VARCHAR(10) NOT NULL
                           CHECK (origin_type IN ('media','person','org')),
        origin_country_code CHAR(2) NOT NULL,
        origin_source_id   UUID REFERENCES sources(id),
        origin_entity_id   UUID REFERENCES persons_orgs(id),
        origin_at          TIMESTAMPTZ NOT NULL,
        origin_confidence  VARCHAR(10) NOT NULL DEFAULT 'medium'
                           CHECK (origin_confidence IN ('high','medium','low')),
        origin_quote       TEXT,
        follower_sequence  JSONB NOT NULL DEFAULT '[]',
        stats_evidence     JSONB,
        detection_method   VARCHAR(20) NOT NULL DEFAULT 'llm'
                           CHECK (detection_method IN ('llm','media_time_fallback')),
        final_review       JSONB,
        revision_log       JSONB NOT NULL DEFAULT '[]',
        human_locked_fields JSONB NOT NULL DEFAULT '[]',
        confirmed_by       UUID REFERENCES users(id),
        confirmed_at       TIMESTAMPTZ,
        dismiss_reason     TEXT,
        is_false_positive  BOOLEAN,
        created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("CREATE INDEX idx_events_status ON agenda_events(status, updated_at DESC)")
    op.execute("CREATE INDEX idx_events_topic ON agenda_events(topic_id)")
    op.execute("CREATE INDEX idx_events_origin_country ON agenda_events(origin_country_code, origin_at DESC)")
    op.execute("CREATE INDEX idx_events_revision_log ON agenda_events USING gin(revision_log)")
    op.execute("COMMENT ON TABLE  agenda_events IS '议程设置事件(首发源/跟随国序列/时滞/统计佐证; 机密级, 涉研判结论)'")
    op.execute("COMMENT ON COLUMN agenda_events.status IS 'watching=观察中/suspected=疑似(自动判定,需人工复核)/confirmed=确认(人工)/dismissed=排除(可重开)/revised=已修正/archived=归档'")
    op.execute("COMMENT ON COLUMN agenda_events.revision_log IS 'JSON 数组: [{seq, revised_at, field, before_value, after_value, trigger_evidence, actor(machine/human), model, prompt_version, rejected}]; 机器自动修正与人工确认/否决均留痕, 人工优先'")
    op.execute("COMMENT ON COLUMN agenda_events.final_review IS 'LLM 终审审查官评分 1-10: <5 自动降为疑似/驳回(REJECTED), ≥5 维持(COMPLETED); 被驳回样本作负例积累'")

    # ---- agenda_event_evidence（2.11） ----
    op.execute("""
    CREATE TABLE agenda_event_evidence (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        event_id      UUID NOT NULL REFERENCES agenda_events(id) ON DELETE CASCADE,
        evidence_type VARCHAR(20) NOT NULL
                      CHECK (evidence_type IN ('origin_article','origin_utterance','follower_article','stat_snapshot')),
        article_id    UUID REFERENCES articles(id),
        entity_id     UUID REFERENCES persons_orgs(id),
        quote         TEXT,
        occurred_at   TIMESTAMPTZ NOT NULL,
        country_code  CHAR(2),
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("CREATE INDEX idx_evidence_event ON agenda_event_evidence(event_id, occurred_at)")
    op.execute("CREATE INDEX idx_evidence_article ON agenda_event_evidence(article_id) WHERE article_id IS NOT NULL")
    op.execute("COMMENT ON TABLE agenda_event_evidence IS '议程设置事件证据链明细(首发表述原文/代表报道/统计快照); N:1 归属 AgendaEvent'")

    # ---- alert_rules（2.13） ----
    op.execute("""
    CREATE TABLE alert_rules (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id         UUID NOT NULL REFERENCES users(id),
        name            VARCHAR(100) NOT NULL,
        country_codes   JSONB NOT NULL,
        topic_id        UUID REFERENCES topics(id),
        keywords        JSONB,
        condition_type  VARCHAR(15) NOT NULL
                        CHECK (condition_type IN ('growth_rate','top_n','neg_ratio')),
        condition_value NUMERIC NOT NULL,
        condition_extra JSONB,
        active_period   VARCHAR(10) NOT NULL DEFAULT 'all_day'
                        CHECK (active_period IN ('all_day','custom')),
        active_hours    JSONB,
        notify_channels JSONB NOT NULL DEFAULT '["inapp","email"]',
        webhook_url     VARCHAR(500),
        enabled         BOOLEAN NOT NULL DEFAULT TRUE,
        last_triggered_at TIMESTAMPTZ,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CHECK (topic_id IS NOT NULL OR keywords IS NOT NULL)
    )
    """)
    op.execute("CREATE INDEX idx_rules_user ON alert_rules(user_id) WHERE enabled = TRUE")
    op.execute("CREATE INDEX idx_rules_eval ON alert_rules(enabled) WHERE enabled = TRUE")
    op.execute("COMMENT ON TABLE alert_rules IS '预警规则(授权用户 ≤50 条/人,估算; 15min 评估周期; 预警风暴单用户 1h>20 条自动合并摘要)'")

    # ---- alerts（2.13） ----
    op.execute("""
    CREATE TABLE alerts (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        rule_id       UUID NOT NULL REFERENCES alert_rules(id),
        user_id       UUID NOT NULL REFERENCES users(id),
        triggered_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        payload       JSONB NOT NULL,
        status        VARCHAR(12) NOT NULL DEFAULT 'unread'
                      CHECK (status IN ('unread','read','archived','suppressed')),
        suppressed_count INT NOT NULL DEFAULT 0,
        notify_result JSONB,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("CREATE INDEX idx_alerts_user_status ON alerts(user_id, status, triggered_at DESC)")
    op.execute("CREATE INDEX idx_alerts_rule ON alerts(rule_id, triggered_at DESC)")
    op.execute("COMMENT ON TABLE alerts IS '预警触发记录(suppressed=防抖合并; webhook 失败 3 次指数退避后降级邮件)'")

    # ---- report_exports（2.14） ----
    op.execute("""
    CREATE TABLE report_exports (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id      UUID NOT NULL REFERENCES users(id),
        template     VARCHAR(20) NOT NULL
                     CHECK (template IN ('topic_deep','compare_brief','periodic_weekly')),
        format       VARCHAR(10) NOT NULL
                     CHECK (format IN ('pdf','docx','markdown','csv')),
        scope        JSONB NOT NULL,
        locale       VARCHAR(10) NOT NULL DEFAULT 'zh-CN',
        status       VARCHAR(12) NOT NULL DEFAULT 'processing'
                     CHECK (status IN ('processing','done','failed')),
        file_path    VARCHAR(500),
        file_size    BIGINT,
        duration_ms  INT,
        error        TEXT,
        watermark    VARCHAR(200) NOT NULL DEFAULT '由 AgendaScope 观澜生成 + 数据口径声明',
        expires_at   TIMESTAMPTZ,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("CREATE INDEX idx_exports_user ON report_exports(user_id, created_at DESC)")
    op.execute("COMMENT ON TABLE report_exports IS '报告导出任务(同步≤60s, 超时转异步队列, 并发>3 排队; 含水印与数据口径声明)'")

    # ---- audit_logs（2.15） ----
    op.execute("""
    CREATE TABLE audit_logs (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        user_id     UUID REFERENCES users(id),
        username    VARCHAR(64),
        action      VARCHAR(50) NOT NULL,
        resource    VARCHAR(300),
        detail      JSONB,
        ip          INET,
        user_agent  VARCHAR(300),
        result      VARCHAR(10) NOT NULL DEFAULT 'success'
                    CHECK (result IN ('success','failure','denied'))
    )
    """)
    op.execute("CREATE INDEX idx_audit_time ON audit_logs(at DESC)")
    op.execute("CREATE INDEX idx_audit_user ON audit_logs(user_id, at DESC)")
    op.execute("CREATE INDEX idx_audit_action ON audit_logs(action, at DESC)")
    op.execute("COMMENT ON TABLE audit_logs IS '审计日志(等保 2.0 三级/保密场景: 登录、查询机密对象、导出、配置与权限变更全量留痕; 只增不改)'")


def downgrade() -> None:
    for table in (
        "audit_logs", "report_exports", "alerts", "alert_rules",
        "agenda_event_evidence", "agenda_events", "agenda_snapshots",
        "topic_articles", "collection_jobs", "articles",
        "persons_orgs", "topics", "sources", "users",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
