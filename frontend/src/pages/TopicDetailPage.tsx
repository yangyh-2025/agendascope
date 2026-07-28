/** 议题详情页（T4.7）：议题卡完整信息 + 修正标注展开 + 相关文章 + 合并建议。 */
import { useCallback, useEffect, useState } from "react";
import ReactECharts from "echarts-for-react";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { AGENDA_EVENT_STATUS_LABEL, LIFECYCLE_LABEL, countryLabel } from "../api/meta";
import {
  getMergeSuggestions,
  getTopic,
  getTopicTimeline,
  listTopicArticles,
  splitTopic,
  type MergeSuggestion,
  type TopicArticleItem,
  type TopicDetail,
  type TopicTimeline,
} from "../api/topics";
import DegradedBadge from "../components/DegradedBadge";
import { degradedKindsOf } from "../components/degraded";
import "./TopicDetailPage.css";

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

function fmtValue(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

export default function TopicDetailPage() {
  const { id = "" } = useParams();
  const [topic, setTopic] = useState<TopicDetail | null>(null);
  const [timeline, setTimeline] = useState<TopicTimeline | null>(null);
  const [articles, setArticles] = useState<TopicArticleItem[]>([]);
  const [articlesDegraded, setArticlesDegraded] = useState(false);
  const [suggestions, setSuggestions] = useState<MergeSuggestion[] | null>(null);
  const [showRevisions, setShowRevisions] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    getTopic(id)
      .then((t) => {
        if (!cancelled) setTopic(t);
      })
      .catch((err) => {
        if (!cancelled) setError(errMsg(err, "议题详情加载失败"));
      });
    getTopicTimeline(id, { days: 7, granularity: "day" })
      .then((t) => {
        if (!cancelled) setTimeline(t);
      })
      .catch(() => {
        /* 趋势图加载失败不阻塞详情 */
      });
    listTopicArticles(id, { page: 1, page_size: 10 })
      .then((r) => {
        if (!cancelled) {
          setArticles(r.items);
          setArticlesDegraded(Boolean(r.degraded));
        }
      })
      .catch(() => {
        if (!cancelled) setArticles([]);
      });
    // 合并建议为 authorized+ 接口，403 时静默隐藏
    getMergeSuggestions(id)
      .then((r) => {
        if (!cancelled) setSuggestions(r.suggestions);
      })
      .catch(() => {
        if (!cancelled) setSuggestions(null);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const handleSplit = useCallback(
    (childId: string) => {
      setActionMsg(null);
      splitTopic(id, childId)
        .then(() => {
          setActionMsg("已提交误并回滚（拆分），议题归属已恢复");
          return getTopic(id).then(setTopic);
        })
        .catch((err) => setActionMsg(errMsg(err, "拆分失败")));
    },
    [id],
  );

  if (error) {
    return (
      <div className="topic-detail-page">
        <p className="page-error" role="alert">{error}</p>
        <Link to="/topics" className="back-link">← 返回议题列表</Link>
      </div>
    );
  }
  if (!topic) return <div className="page-loading">加载中…</div>;

  const revisions = topic.revision_log ?? [];
  const degradedKinds = degradedKindsOf(topic);
  const points = timeline?.points ?? [];
  const trendOption = {
    grid: { left: 40, right: 16, top: 16, bottom: 24 },
    tooltip: { trigger: "axis" },
    xAxis: {
      type: "category",
      data: points.map((p) => p.window_start.slice(0, 10)),
      axisLabel: { color: "#9DB2D0" },
    },
    yAxis: { type: "value", axisLabel: { color: "#9DB2D0" }, splitLine: { lineStyle: { color: "#1e3a5f" } } },
    series: [
      {
        name: "报道量",
        type: "line",
        smooth: true,
        areaStyle: { opacity: 0.25 },
        itemStyle: { color: "#3B82F6" },
        data: points.map((p) => p.article_count),
      },
    ],
  };

  return (
    <div className="topic-detail-page">
      <Link to="/topics" className="back-link">← 返回议题列表</Link>

      {topic.redirect_topic_id && (
        <p className="merged-redirect">
          该议题已合并至其他议题，<Link to={`/topics/${topic.redirect_topic_id}`}>查看合并后议题</Link>。
        </p>
      )}

      <div className="topic-detail-card">
        <div className="topic-detail-head">
          <h1>{topic.name_zh || topic.name}</h1>
          <span className="category-tag">{topic.topic_category}</span>
          <span className={`lifecycle-tag ${topic.lifecycle_state}`}>
            {LIFECYCLE_LABEL[topic.lifecycle_state] ?? topic.lifecycle_state}
          </span>
          {degradedKinds.map((k) => (
            <DegradedBadge key={k} kind={k} />
          ))}
        </div>
        {topic.name_zh && topic.name !== topic.name_zh && (
          <p className="topic-name-auto">原名：{topic.name}</p>
        )}
        {topic.summary_zh && <p className="topic-summary-text">{topic.summary_zh}</p>}
        <div className="topic-meta-grid">
          <div><span>生命周期置信度</span><b>{topic.confidence}</b></div>
          <div><span>覆盖国家</span><b>{topic.country_scope.map(countryLabel).join("、") || "—"}</b></div>
          <div><span>首次出现</span><b>{topic.first_seen_at?.slice(0, 10) ?? "—"}</b></div>
          <div><span>最近活跃</span><b>{topic.last_seen_at?.slice(0, 16).replace("T", " ") ?? "—"}</b></div>
          {topic.stats_24h && (
            <>
              <div><span>24h 报道量</span><b>{topic.stats_24h.article_count}</b></div>
              <div><span>24h 媒体数</span><b>{topic.stats_24h.media_count}</b></div>
            </>
          )}
        </div>
        {topic.keywords.length > 0 && (
          <div className="topic-keywords">
            {topic.keywords.map((kw) => (
              <span key={kw} className="keyword-chip">{kw}</span>
            ))}
          </div>
        )}

        <div className="revision-row">
          <button
            className="revision-badge"
            onClick={() => setShowRevisions((v) => !v)}
            aria-expanded={showRevisions}
          >
            修正记录 {revisions.length} 条 {showRevisions ? "▲" : "▼"}
          </button>
        </div>
        {showRevisions && (
          <div className="revision-list">
            {revisions.length === 0 && <p className="drawer-empty">暂无修正记录</p>}
            {revisions.map((r) => (
              <div key={r.seq} className="revision-item">
                <span className="revision-field">{r.field}</span>
                <span className="revision-change">{fmtValue(r.before_value)} → {fmtValue(r.after_value)}</span>
                <span className="revision-meta">
                  {r.actor === "human" ? "人工" : "机器"} · {r.revised_at?.slice(0, 16).replace("T", " ")}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {topic.agenda_events.length > 0 && (
        <div className="topic-detail-card">
          <h2>关联议程设置事件</h2>
          <ul className="agenda-event-list">
            {topic.agenda_events.map((ev) => (
              <li key={ev.id}>
                <Link to={`/events/${ev.id}`}>
                  {AGENDA_EVENT_STATUS_LABEL[ev.status] ?? ev.status} · 首发国 {countryLabel(ev.origin_country_code)}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      {points.length > 0 && (
        <div className="topic-detail-card">
          <h2>近 7 天报道趋势</h2>
          <ReactECharts option={trendOption} style={{ height: 220 }} />
        </div>
      )}

      <div className="topic-detail-card">
        <h2>相关文章</h2>
        {articlesDegraded && <DegradedBadge kind="snapshot_outdated" reason="全文检索不可用，已降级为基础匹配" />}
        {articles.length === 0 && <p className="drawer-empty">暂无相关文章</p>}
        <ul className="article-list">
          {articles.map((a) => (
            <li key={a.id} className="article-item">
              <a href={a.url} target="_blank" rel="noreferrer" className="article-title">{a.title}</a>
              {a.excerpt && <p className="article-excerpt">{a.excerpt}…</p>}
              <span className="article-meta">
                {a.source_name ?? "未知来源"} · {countryLabel(a.country_code)} · {a.published_at?.slice(0, 16).replace("T", " ") ?? ""}
              </span>
            </li>
          ))}
        </ul>
      </div>

      {suggestions && suggestions.length > 0 && (
        <div className="topic-detail-card">
          <h2>合并建议</h2>
          <p className="merge-hint">以下议题与本议题相似度较高，请人工确认是否归并（系统不会自动合并）。</p>
          <ul className="merge-list">
            {suggestions.map((s) => (
              <li key={s.topic_id}>
                <Link to={`/topics/${s.topic_id}`}>{s.name_zh || s.name}</Link>
                <span>相似度 {(s.similarity * 100).toFixed(1)}%</span>
                {s.in_no_merge_list && <span className="no-merge-tag">已标记不可合并</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {topic.merged_from.length > 0 && (
        <div className="topic-detail-card">
          <h2>已并入的源议题</h2>
          <p className="merge-hint">如确认系误并，可执行回滚拆分（双方将进入不可合并名单）。</p>
          <ul className="merge-list">
            {topic.merged_from.map((childId) => (
              <li key={childId}>
                <Link to={`/topics/${childId}`}>{childId}</Link>
                <button className="as-btn-ghost" onClick={() => handleSplit(childId)}>
                  误并回滚（拆分）
                </button>
              </li>
            ))}
          </ul>
          {actionMsg && <p className="status-msg">{actionMsg}</p>}
        </div>
      )}
    </div>
  );
}
