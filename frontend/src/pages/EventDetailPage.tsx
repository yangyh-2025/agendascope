/** 议程设置事件详情页（T4.9）：世界地图流向动画 + 检验结果卡 + 修正留痕。 */
import { useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import {
  getAgendaEvent,
  getAgendaEventChain,
  type AgendaEventDetail,
} from "../api/agendaEvents";
import { AGENDA_EVENT_STATUS_LABEL, countryLabel } from "../api/meta";
import DegradedBadge from "../components/DegradedBadge";
import { countryCenter, registerWorldMap } from "../map/worldMap";
import "./EventDetailPage.css";

/** 议程设置事件高亮色：中国红（视觉规范中红色仅用于预警与议程设置事件）。 */
const EVENT_RED = "#C8102E";
const FOLLOWER_BLUE = "#3B82F6";
/** 统计检验样本量下限：低于该值视为数据量不足，不下显著性结论。 */
const MIN_SAMPLE_SIZE = 30;

interface FlowEdge {
  from: string;
  to: string;
  lag_hours: number;
}

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

export default function EventDetailPage() {
  const { id = "" } = useParams();
  const [event, setEvent] = useState<AgendaEventDetail | null>(null);
  const [edges, setEdges] = useState<FlowEdge[]>([]);
  const [error, setError] = useState<string | null>(null);

  useMemo(() => {
    registerWorldMap();
    return true;
  }, []);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    getAgendaEvent(id)
      .then(async (ev) => {
        if (cancelled) return;
        setEvent(ev);
        // 优先用 chain 端点的传播边；不可用时由 follower_sequence 兜底构图
        try {
          const chain = await getAgendaEventChain(id);
          if (!cancelled && chain.edges.length > 0) {
            setEdges(chain.edges.map((e) => ({ from: e.from_country, to: e.to_country, lag_hours: e.lag_hours })));
            return;
          }
        } catch {
          /* chain 端点未上线/失败时走兜底 */
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
        if (!cancelled) setError(errMsg(err, "事件详情加载失败"));
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

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
    return {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "item",
        formatter: (params: { seriesType: string; data?: { lag_hours?: number; from?: string; to?: string; name?: string } }) => {
          if (params.seriesType === "lines" && params.data) {
            return `${countryLabel(params.data.from)} → ${countryLabel(params.data.to)}<br/>时滞 ${params.data.lag_hours?.toFixed(1)} 小时`;
          }
          return params.data?.name ?? "";
        },
      },
      geo: {
        map: "world",
        roam: true,
        silent: true,
        itemStyle: { areaColor: "#132743", borderColor: "#274B84", borderWidth: 0.5 },
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
            symbolSize: 6,
            color: EVENT_RED,
          },
          lineStyle: { color: EVENT_RED, width: 1.5, opacity: 0.55, curveness: 0.25 },
          data: lineData,
        },
        {
          type: "effectScatter",
          coordinateSystem: "geo",
          zlevel: 3,
          rippleEffect: { brushType: "stroke" },
          symbolSize: 10,
          itemStyle: { color: EVENT_RED },
          label: {
            show: true,
            position: "right",
            formatter: (p: { data: { label: string } }) => p.data.label,
            color: "#F0F4FA",
            fontSize: 11,
          },
          data: points.has(origin)
            ? [{ name: countryLabel(origin), value: points.get(origin)!, label: `首发：${countryLabel(origin)}` }]
            : [],
        },
        {
          type: "scatter",
          coordinateSystem: "geo",
          zlevel: 3,
          symbolSize: 7,
          itemStyle: { color: FOLLOWER_BLUE },
          label: {
            show: true,
            position: "right",
            formatter: (p: { data: { label: string } }) => p.data.label,
            color: "#9DB2D0",
            fontSize: 10,
          },
          data: [...points.entries()]
            .filter(([code]) => code !== origin)
            .map(([code, coord]) => ({
              name: countryLabel(code),
              value: coord,
              label: `${countryLabel(code)} +${edges.find((e) => e.to === code)?.lag_hours.toFixed(1) ?? "?"}h`,
            })),
        },
      ],
    };
  }, [event, edges]);

  if (error) {
    return (
      <div className="event-detail-page">
        <p className="page-error" role="alert">{error}</p>
        <Link to="/events" className="back-link">← 返回事件列表</Link>
      </div>
    );
  }
  if (!event) return <div className="page-loading">加载中…</div>;

  const stats = event.stats_evidence;
  const sampleInsufficient = !stats || (stats.sample_size ?? 0) < MIN_SAMPLE_SIZE;
  const tests: { name: string; p: number | null; extra?: string }[] = stats
    ? [
        { name: "互相关（xcorr）", p: stats.xcorr?.p ?? null, extra: stats.xcorr ? `最佳时滞 ${stats.xcorr.best_lag_days} 天 · r=${stats.xcorr.r?.toFixed(3)}` : undefined },
        { name: "格兰杰检验", p: stats.granger?.p ?? null, extra: stats.granger?.direction ? `方向：${stats.granger.direction}` : undefined },
        { name: "QAP 相关", p: stats.qap?.p ?? null, extra: stats.qap ? `r=${stats.qap.r?.toFixed(3)}` : undefined },
      ]
    : [];

  return (
    <div className="event-detail-page">
      <Link to="/events" className="back-link">← 返回事件列表</Link>

      <div className="event-detail-card event-head-card">
        <div className="event-detail-head">
          <h1>{event.topic_name ?? "议程设置事件"}</h1>
          <span className="event-status-tag">{AGENDA_EVENT_STATUS_LABEL[event.status] ?? event.status}</span>
          {event.origin_confidence === "low" && <DegradedBadge kind="origin_needs_review" />}
        </div>
        <div className="event-meta-grid">
          <div><span>首发国</span><b className="origin-red">{countryLabel(event.origin_country_code)}</b></div>
          <div><span>首发时间</span><b>{event.origin_at?.slice(0, 16).replace("T", " ") ?? "—"}</b></div>
          <div><span>首发类型</span><b>{event.origin_type}</b></div>
          <div><span>首发媒体</span><b>{event.origin_source?.name ?? "—"}</b></div>
          <div><span>首发实体</span><b>{event.origin_entity?.name ?? "—"}</b></div>
          <div><span>检测方式</span><b>{event.detection_method}</b></div>
          <div><span>跟随国数</span><b>{event.follower_sequence?.length ?? 0}</b></div>
          <div><span>判定轮次</span><b>第 {event.round_no} 轮</b></div>
        </div>
        {event.origin_quote && <blockquote className="origin-quote">“{event.origin_quote}”</blockquote>}
        {event.topic_id && (
          <p className="topic-link-row">关联议题：<Link to={`/topics/${event.topic_id}`}>{event.topic_name ?? event.topic_id}</Link></p>
        )}
      </div>

      {flowOption && edges.length > 0 && (
        <div className="event-detail-card">
          <h2>跨国传播流向（首发国 → 跟随国，箭头标注时滞）</h2>
          <ReactECharts option={flowOption} style={{ height: "52vh" }} />
        </div>
      )}

      <div className="event-detail-card">
        <h2>统计检验结果</h2>
        {sampleInsufficient ? (
          <p className="insufficient">数据量不足（样本 {stats?.sample_size ?? 0}，需 ≥{MIN_SAMPLE_SIZE}），暂不下显著性结论。</p>
        ) : (
          <div className="stats-grid">
            {tests.map((t) => (
              <div key={t.name} className="stat-test">
                <span className="stat-test-name">{t.name}</span>
                {t.p != null && t.p < 0.05 ? (
                  <span className="sig-yes">显著（p={t.p.toFixed(4)} &lt; 0.05）</span>
                ) : (
                  <span className="sig-no">不显著{t.p != null ? `（p=${t.p.toFixed(4)}）` : ""}</span>
                )}
                {t.extra && <span className="stat-extra">{t.extra}</span>}
              </div>
            ))}
            <div className="stat-test">
              <span className="stat-test-name">样本量</span>
              <span className="stat-extra">{stats?.sample_size}</span>
            </div>
          </div>
        )}
        <p className="causality-note">统计关联≠因果——检验结果仅反映报道曲线的统计相关性，不构成因果证据。</p>
        {stats?.disclaimer && <p className="stat-disclaimer">{stats.disclaimer}</p>}
      </div>

      {event.final_review && (
        <div className="event-detail-card">
          <h2>终审结论</h2>
          <div className="final-review">
            <b>{event.final_review.score}/10 · {event.final_review.verdict}</b>
            {event.final_review.reasoning && <p>{event.final_review.reasoning}</p>}
            {event.final_review.concerns && event.final_review.concerns.length > 0 && (
              <ul>
                {event.final_review.concerns.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      {event.follower_sequence && event.follower_sequence.length > 0 && (
        <div className="event-detail-card">
          <h2>跟随序列</h2>
          <table className="follower-table">
            <thead>
              <tr><th>国家</th><th>首发媒体</th><th>时滞（小时）</th></tr>
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

      {event.revision_log && event.revision_log.length > 0 && (
        <div className="event-detail-card">
          <h2>修正留痕</h2>
          <div className="revision-list">
            {event.revision_log.map((r) => (
              <div key={r.seq} className={`revision-item ${r.rejected ? "rejected" : ""}`}>
                <span className="revision-field">{r.field}</span>
                <span className="revision-change">{String(r.before_value)} → {String(r.after_value)}</span>
                <span className="revision-meta">
                  {r.actor === "human" ? "人工" : "机器"} · {r.revised_at?.slice(0, 16).replace("T", " ")}
                  {r.rejected && " · 已否决"}
                </span>
              </div>
            ))}
          </div>
          <p className="revision-ops-hint">人工确认/否决操作请前往 <Link to="/revisions">修正历史</Link> 页。</p>
        </div>
      )}
    </div>
  );
}
