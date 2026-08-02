/** 议题详情页(T4.7):议题卡完整信息 + 修正标注展开 + 相关文章 + 合并建议。 */
import { useCallback, useEffect, useMemo, useState } from "react";
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
import {
  getAgendaEvent,
  getAgendaEventChain,
  type AgendaEventDetail,
} from "../api/agendaEvents";
import DegradedBadge from "../components/DegradedBadge";
import { degradedKindsOf } from "../components/degraded";
import { countryCenter, registerWorldMap } from "../map/worldMap";
import "./TopicDetailPage.css";

const EVENT_RED = "#C8102E";
const FOLLOWER_BLUE = "#1A4FA0";

const ORIGIN_TYPE_LABEL: Record<string, string> = {
  media: "媒体首发",
  person: "人物首发",
  gov: "政府首发",
  org: "机构首发",
  official: "官方首发",
  social: "社交媒体首发",
};

const DETECTION_METHOD_LABEL: Record<string, string> = {
  llm: "LLM 判定",
  llm_judged: "LLM 判定",
  heuristic: "规则判定",
  manual: "人工标注",
  auto: "自动检测",
  statistical: "统计检测",
};

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
  const [mergedTopics, setMergedTopics] = useState<Record<string, TopicDetail | null>>({});

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

  /* 拉取所有"已并入源议题"的完整详情 */
  useEffect(() => {
    if (!topic || topic.merged_from.length === 0) {
      setMergedTopics({});
      return;
    }
    let cancelled = false;
    Promise.all(
      topic.merged_from.map(async (childId) => {
        try {
          const t = await getTopic(childId);
          return [childId, t] as const;
        } catch {
          return [childId, null] as const;
        }
      }),
    ).then((entries) => {
      if (!cancelled) {
        setMergedTopics(Object.fromEntries(entries));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [topic]);

  const handleSplit = useCallback(
    (childId: string) => {
      setActionMsg(null);
      splitTopic(id, childId)
        .then(() => {
          setActionMsg("已提交误并回滚(拆分),议题归属已恢复");
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
    animationDuration: 600,
    animationEasing: "cubicOut",
    tooltip: {
      trigger: "axis",
      backgroundColor: "#FFFFFF",
      borderColor: "#E4E9F2",
      borderWidth: 1,
      textStyle: { color: "#1F2D3D", fontSize: 13 },
      extraCssText: "box-shadow: 0 4px 16px rgba(15, 61, 138, 0.12); border-radius: 8px;",
    },
    xAxis: {
      type: "category",
      data: points.map((p) => p.window_start.slice(0, 10)),
      axisLabel: { color: "#9AA8BB", fontSize: 12 },
      axisLine: { lineStyle: { color: "#E4E9F2" } },
      axisTick: { show: false },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: "#9AA8BB", fontSize: 12 },
      splitLine: { lineStyle: { color: "#E4E9F2", type: "dashed" } },
    },
    series: [
      {
        name: "报道量",
        type: "line",
        smooth: true,
        symbol: "circle",
        symbolSize: 6,
        lineStyle: { width: 2, color: "#1A4FA0" },
        itemStyle: { color: "#1A4FA0", borderColor: "#FFFFFF", borderWidth: 2 },
        areaStyle: {
          color: {
            type: "linear",
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: "rgba(26, 79, 160, 0.25)" },
              { offset: 1, color: "rgba(26, 79, 160, 0.02)" },
            ],
          },
        },
        data: points.map((p) => p.article_count),
      },
    ],
  };

  return (
    <div className="topic-detail-page">
      <Link to="/topics" className="back-link">← 返回议题列表</Link>

      {topic.redirect_topic_id && (
        <p className="merged-redirect">
          该议题已合并至其他议题,<Link to={`/topics/${topic.redirect_topic_id}`}>查看合并后议题</Link>。
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
          <p className="topic-name-auto">原名:{topic.name}</p>
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

      {/* 关联议程事件:直接展开显示完整详情 */}
      {topic.agenda_events.length > 0 && (
        <div className="topic-detail-card">
          <h2>关联议程设置事件({topic.agenda_events.length})</h2>
          <div className="agenda-event-inline-list">
            {topic.agenda_events.map((ev) => (
              <AgendaEventInline key={ev.id} eventId={ev.id} />
            ))}
          </div>
        </div>
      )}

      {points.length > 0 && (
        <div className="topic-detail-card">
          <h2>近 7 天报道趋势</h2>
          <ReactECharts option={trendOption} style={{ height: 240 }} />
        </div>
      )}

      <div className="topic-detail-card">
        <h2>相关文章</h2>
        {articlesDegraded && <DegradedBadge kind="snapshot_outdated" reason="全文检索不可用,已降级为基础匹配" />}
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
          <p className="merge-hint">以下议题与本议题相似度较高,请人工确认是否归并(系统不会自动合并)。</p>
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
          <h2>已并入的源议题({topic.merged_from.length})</h2>
          <p className="merge-hint">如确认系误并,可执行回滚拆分(双方将进入不可合并名单)。</p>
          <div className="merged-topic-list">
            {topic.merged_from.map((childId) => {
              const child = mergedTopics[childId];
              return (
                <div key={childId} className="merged-topic-card">
                  {child ? (
                    <>
                      <div className="merged-topic-head">
                        <h3 className="merged-topic-name">{child.name_zh || child.name}</h3>
                        <span className={`lifecycle-tag ${child.lifecycle_state}`}>
                          {LIFECYCLE_LABEL[child.lifecycle_state] ?? child.lifecycle_state}
                        </span>
                        <span className="category-tag">{child.topic_category}</span>
                      </div>
                      {child.summary_zh && (
                        <p className="merged-topic-summary">{child.summary_zh}</p>
                      )}
                      <div className="merged-topic-meta">
                        <span>覆盖 {child.country_scope.map(countryLabel).join("、") || "—"}</span>
                        {child.stats_24h && (
                          <>
                            <span>·</span>
                            <span>24h {child.stats_24h.article_count} 篇</span>
                          </>
                        )}
                        <span>·</span>
                        <span>首次 {child.first_seen_at?.slice(0, 10) ?? "—"}</span>
                      </div>
                    </>
                  ) : (
                    <p className="merged-topic-loading">加载源议题信息中…</p>
                  )}
                  <div className="merged-topic-ops">
                    <Link to={`/topics/${childId}`} className="as-btn-ghost">
                      查看完整详情 →
                    </Link>
                    <button className="as-btn-danger" onClick={() => handleSplit(childId)}>
                      误并回滚(拆分)
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
          {actionMsg && <p className="status-msg">{actionMsg}</p>}
        </div>
      )}
    </div>
  );
}

/** 内嵌议程事件详情卡(不跳路由,直接显示元信息+流向图+跟随序列)。 */
function AgendaEventInline({ eventId }: { eventId: string }) {
  const [event, setEvent] = useState<AgendaEventDetail | null>(null);
  const [edges, setEdges] = useState<{ from: string; to: string; lag_hours: number }[]>([]);
  const [error, setError] = useState<string | null>(null);

  useMemo(() => {
    registerWorldMap();
    return true;
  }, []);

  useEffect(() => {
    let cancelled = false;
    setEvent(null);
    setEdges([]);
    setError(null);
    getAgendaEvent(eventId)
      .then(async (ev) => {
        if (cancelled) return;
        setEvent(ev);
        try {
          const chain = await getAgendaEventChain(eventId);
          if (!cancelled && chain.edges.length > 0) {
            setEdges(chain.edges.map((e) => ({ from: e.from_country, to: e.to_country, lag_hours: e.lag_hours })));
            return;
          }
        } catch {
          /* chain 失败走兜底 */
        }
        if (!cancelled) {
          setEdges(
            (ev.follower_sequence ?? []).map((f) => ({
              from: ev.origin_country_code,
              to: f.country_code,
              lag_hours: f.lag_hours,
            })),
          );
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "事件详情加载失败");
      });
    return () => {
      cancelled = true;
    };
  }, [eventId]);

  const flowOption = useMemo(() => {
    if (!event) return null;
    const origin = event.origin_country_code;
    const points = new Map<string, [number, number]>();
    for (const code of [origin, ...edges.map((e) => e.to)]) {
      const c = countryCenter(code);
      if (c) points.set(code, c);
    }
    const lineData = edges
      .filter((e) => points.has(e.from) && points.has(e.to))
      .map((e) => ({
        coords: [points.get(e.from)!, points.get(e.to)!],
        lag_hours: e.lag_hours,
        from: e.from,
        to: e.to,
      }));
    if (lineData.length === 0) return null;
    return {
      backgroundColor: "transparent",
      animationDuration: 600,
      tooltip: {
        trigger: "item",
        backgroundColor: "#FFFFFF",
        borderColor: "#E4E9F2",
        borderWidth: 1,
        textStyle: { color: "#1F2D3D", fontSize: 12 },
        extraCssText: "box-shadow: 0 4px 16px rgba(15, 61, 138, 0.12); border-radius: 8px;",
        formatter: (params: { seriesType: string; data?: { lag_hours?: number; from?: string; to?: string; name?: string } }) => {
          if (params.seriesType === "lines" && params.data) {
            return `${countryLabel(params.data.from)} → ${countryLabel(params.data.to)}<br/>时滞 ${params.data.lag_hours?.toFixed(1)} 小时`;
          }
          return params.data?.name ?? "";
        },
      },
      geo: {
        map: "world",
        roam: false,
        silent: true,
        itemStyle: { areaColor: "#F0F4FA", borderColor: "#FFFFFF", borderWidth: 0.6 },
        emphasis: { disabled: true },
      },
      series: [
        {
          type: "lines",
          coordinateSystem: "geo",
          zlevel: 2,
          effect: {
            show: true,
            period: 5,
            trailLength: 0.4,
            symbol: "arrow",
            symbolSize: 5,
            color: EVENT_RED,
          },
          lineStyle: { color: EVENT_RED, width: 1.2, opacity: 0.55, curveness: 0.25 },
          data: lineData,
        },
        {
          type: "effectScatter",
          coordinateSystem: "geo",
          zlevel: 3,
          rippleEffect: { brushType: "stroke" },
          symbolSize: 8,
          itemStyle: { color: EVENT_RED },
          label: {
            show: true,
            position: "right",
            formatter: (p: { data: { label: string } }) => p.data.label,
            color: "#1F2D3D",
            fontSize: 10,
            fontWeight: 600,
            textBorderColor: "#FFFFFF",
            textBorderWidth: 2,
          },
          data: points.has(origin)
            ? [{ name: countryLabel(origin), value: points.get(origin)!, label: `首发:${countryLabel(origin)}` }]
            : [],
        },
        {
          type: "scatter",
          coordinateSystem: "geo",
          zlevel: 3,
          symbolSize: 5,
          itemStyle: { color: FOLLOWER_BLUE },
          data: [...points.entries()]
            .filter(([code]) => code !== origin)
            .map(([, coord]) => ({ value: coord })),
        },
      ],
    };
  }, [event, edges]);

  if (error) {
    return <p className="page-error" role="alert">{error}</p>;
  }
  if (!event) {
    return <div className="agenda-event-inline loading">事件详情加载中…</div>;
  }

  return (
    <div className="agenda-event-inline">
      <div className="agenda-event-inline-head">
        <span className={`event-status st-${event.status}`}>
          {AGENDA_EVENT_STATUS_LABEL[event.status] ?? event.status}
        </span>
        <h3>{event.topic_name ?? "议程设置事件"}</h3>
        {event.origin_confidence === "low" && <DegradedBadge kind="origin_needs_review" />}
      </div>

      <div className="agenda-event-inline-meta">
        <div><span>首发国</span><b className="origin-red">{countryLabel(event.origin_country_code)}</b></div>
        <div><span>首发时间</span><b>{event.origin_at?.slice(0, 16).replace("T", " ") ?? "—"}</b></div>
        <div><span>首发类型</span><b>{ORIGIN_TYPE_LABEL[event.origin_type] ?? event.origin_type}</b></div>
        <div><span>首发媒体</span><b>{event.origin_source?.name ?? "—"}</b></div>
        <div><span>检测方式</span><b>{DETECTION_METHOD_LABEL[event.detection_method] ?? event.detection_method}</b></div>
        <div><span>跟随国数</span><b>{event.follower_sequence?.length ?? 0}</b></div>
      </div>

      {event.origin_quote && (
        <blockquote className="agenda-event-inline-quote">"{event.origin_quote}"</blockquote>
      )}

      {flowOption && (
        <div className="agenda-event-inline-map">
          <h4>跨国传播流向</h4>
          <ReactECharts option={flowOption} style={{ height: 280 }} />
        </div>
      )}

      {event.follower_sequence && event.follower_sequence.length > 0 && (
        <div className="agenda-event-inline-followers">
          <h4>跟随序列({event.follower_sequence.length})</h4>
          <table className="follower-table">
            <thead>
              <tr><th>国家</th><th>首发媒体</th><th>时滞(小时)</th></tr>
            </thead>
            <tbody>
              {event.follower_sequence.map((f) => (
                <tr key={f.country_code}>
                  <td>{countryLabel(f.country_code)}</td>
                  <td>{f.first_media_name ?? f.first_media ?? "—"}</td>
                  <td>{f.lag_hours.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {event.final_review && (
        <div className="agenda-event-inline-review">
          <h4>终审结论</h4>
          <b>{event.final_review.score}/10 · {event.final_review.verdict}</b>
          {event.final_review.reasoning && <p>{event.final_review.reasoning}</p>}
        </div>
      )}
    </div>
  );
}
