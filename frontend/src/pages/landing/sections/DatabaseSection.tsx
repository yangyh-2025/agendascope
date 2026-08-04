import ScrollReveal from "../components/ScrollReveal";

/** 数据库流水线步骤（案例场景：一条新闻从采集到看板的完整旅程）。 */
const PIPELINE_STEPS = [
  {
    stage: "采集",
    worker: "collector",
    table: "articles",
    sql: `INSERT INTO articles (id, title, content,
  source_id, country_code, published_at)
VALUES (
  '8f3e...', 'Rubio meets Kallas on...',
  'US Secretary of State Marco Rubio...',
  'reuters-001', 'US', '2026-08-05 14:23'
);`,
    note: "Reuters 原文抓取",
  },
  {
    stage: "NLP",
    worker: "nlp-worker",
    table: "article_processing",
    sql: `UPDATE article_processing
SET nlp_status = 'done',
    nlp_finished_at = now()
WHERE article_id = '8f3e...';

-- articles.embedding 同步写入
-- 1024 维向量`,
    note: "bge-m3 向量化 + 语言/情感",
  },
  {
    stage: "实体抽取",
    worker: "entity-worker",
    table: "article_entities",
    sql: `INSERT INTO article_entities
  (article_id, entity_id, mention_count,
   is_primary_subject, extracted_by)
VALUES
  ('8f3e...', 'rubio-id', 3, true, 'seed_match'),
  ('8f3e...', 'kallas-id', 2, true, 'seed_match');`,
    note: "种子实体精确匹配",
  },
  {
    stage: "聚类",
    worker: "cluster-worker",
    table: "topic_articles",
    sql: `INSERT INTO topic_articles
  (topic_id, article_id, weight, assign_method)
VALUES (
  'us-eu-indopac-id', '8f3e...',
  0.92, 'online'
);

-- 议题: 美欧印太安全合作`,
    note: "HNSW 相似度归并到现有议题",
  },
  {
    stage: "关系抽取",
    worker: "relation-worker",
    table: "entity_relations + relation_evidences",
    sql: `INSERT INTO entity_relations
  (subject_entity_id, object_entity_id,
   relation_type, confidence, evidence_count)
VALUES (
  'rubio-id', 'kallas-id', 'meets', 0.9, 1
)
ON CONFLICT ... DO UPDATE
SET evidence_count = evidence_count + 1;`,
    note: "LLM 抽取 + evidence_quote 原文校验",
  },
  {
    stage: "快照聚合",
    worker: "snapshot-worker",
    table: "topic_snapshots / entity_snapshots",
    sql: `INSERT INTO entity_snapshots
  (entity_id, window_start, granularity,
   mention_count, article_count)
VALUES (
  'rubio-id', '2026-08-05 14:00',
  'hour', 12, 8
);`,
    note: "小时/日级预聚合，看板秒查",
  },
];

export default function DatabaseSection() {
  return (
    <section className="lp-section lp-database" id="database">
      <div className="lp-container">
        <ScrollReveal>
          <div className="lp-section-eyebrow">数据库 · 单一事实源</div>
          <h2 className="lp-section-title">
            让每条新闻
            <br />
            都可追溯
          </h2>
          <p className="lp-section-lede">
            L0 原始层 → L1 加工层 → L2 事实层 → L3 快照层。
            <br />
            每一步处理都显式落库，失败可重跑、进度可查询、来源可回溯。
          </p>
        </ScrollReveal>

        {/* 案例场景：一条新闻的完整旅程 */}
        <ScrollReveal delay={120}>
          <div className="lp-db-case">
            <div className="lp-db-case-header">
              <span className="lp-db-case-badge">案例场景</span>
              <span className="lp-db-case-title">
                2026-08-05 14:23 · Reuters 发布 "Rubio meets Kallas on Indo-Pacific security"
              </span>
            </div>
            <div className="lp-db-pipeline">
              {PIPELINE_STEPS.map((step, idx) => (
                <div key={idx} className="lp-db-step">
                  <div className="lp-db-step-header">
                    <span className="lp-db-step-num">{idx + 1}</span>
                    <span className="lp-db-step-stage">{step.stage}</span>
                    <span className="lp-db-step-worker">{step.worker}</span>
                  </div>
                  <div className="lp-db-step-table">{step.table}</div>
                  <pre className="lp-db-step-sql">{step.sql}</pre>
                  <div className="lp-db-step-note">{step.note}</div>
                  {idx < PIPELINE_STEPS.length - 1 && (
                    <div className="lp-db-step-arrow" aria-hidden="true">
                      →
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </ScrollReveal>

        {/* 架构分层示意 */}
        <ScrollReveal delay={240}>
          <div className="lp-db-layers">
            <div className="lp-db-layer">
              <div className="lp-db-layer-tag lp-db-layer-l0">L0 原始层</div>
              <div className="lp-db-layer-name">articles / sources / collection_jobs</div>
              <div className="lp-db-layer-desc">采集即入库，原文+元数据不丢</div>
            </div>
            <div className="lp-db-layer">
              <div className="lp-db-layer-tag lp-db-layer-l1">L1 加工层</div>
              <div className="lp-db-layer-name">article_processing / article_entities / worker_tasks</div>
              <div className="lp-db-layer-desc">状态机跟踪每一步，失败可重跑，分布式 worker 队列</div>
            </div>
            <div className="lp-db-layer">
              <div className="lp-db-layer-tag lp-db-layer-l2">L2 事实层</div>
              <div className="lp-db-layer-name">topics / agenda_events / persons_orgs / entity_relations</div>
              <div className="lp-db-layer-desc">对外呈现的事实：议题、事件、实体、关系</div>
            </div>
            <div className="lp-db-layer">
              <div className="lp-db-layer-tag lp-db-layer-l3">L3 快照层</div>
              <div className="lp-db-layer-name">topic_snapshots / entity_snapshots / source_snapshots</div>
              <div className="lp-db-layer-desc">预聚合时序数据，看板查询不再现算</div>
            </div>
          </div>
        </ScrollReveal>

        {/* 与 GDELT 对比 */}
        <ScrollReveal delay={360}>
          <div className="lp-db-compare">
            <div className="lp-db-compare-title">借鉴 GDELT，超越 GDELT</div>
            <div className="lp-db-compare-grid">
              <div className="lp-db-compare-item">
                <span className="lp-db-compare-check">✓</span>
                <span>显式处理状态机（GDELT 没有）：每篇文章 NLP/聚类/关系抽取状态可查</span>
              </div>
              <div className="lp-db-compare-item">
                <span className="lp-db-compare-check">✓</span>
                <span>分布式任务队列（GDELT 没有）：worker 可任意分布式部署</span>
              </div>
              <div className="lp-db-compare-item">
                <span className="lp-db-compare-check">✓</span>
                <span>议题生命周期事件溯源（GDELT 没有）：每次合并/重命名/首发修正留痕</span>
              </div>
              <div className="lp-db-compare-item">
                <span className="lp-db-compare-check">✓</span>
                <span>多元实体关系（GDELT 仅二元）：支持 N 实体事件的复杂关联</span>
              </div>
            </div>
          </div>
        </ScrollReveal>
      </div>
    </section>
  );
}
