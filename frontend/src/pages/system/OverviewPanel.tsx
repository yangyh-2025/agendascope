/** 系统概览面板：CPU/内存/磁盘、当日采集量、队列积压、延迟 P95（近 24h）。 */
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../../api/client";
import { fetchSystemOverview, type OverviewMetrics } from "../../api/systemAdmin";

function metric(value: number | null | undefined, suffix = ""): string {
  return value === null || value === undefined ? "—" : `${value}${suffix}`;
}

export default function OverviewPanel() {
  const [data, setData] = useState<OverviewMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    fetchSystemOverview()
      .then(setData)
      .catch((err) => setError(err instanceof ApiError ? err.message : "系统概览加载失败"));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const metricsUnavailable = data?.metrics_status === "unavailable";

  return (
    <section className="sys-panel">
      <div className="sys-panel-head">
        <h3>系统概览</h3>
        <button type="button" className="as-btn-ghost" onClick={load}>刷新</button>
      </div>
      {error && <p className="page-error" role="alert">{error}</p>}
      {!error && !data && <p className="page-loading">加载中…</p>}
      {data && (
        <>
          <div className="metric-grid">
            <div className="metric-card">
              <span className="metric-label">CPU</span>
              <b className="metric-value">
                {metricsUnavailable ? "指标不可用" : metric(data.cpu?.percent, "%")}
              </b>
              {!metricsUnavailable && data.cpu && (
                <span className="metric-sub">{data.cpu.cores} 核</span>
              )}
            </div>
            <div className="metric-card">
              <span className="metric-label">内存</span>
              <b className="metric-value">
                {metricsUnavailable ? "指标不可用" : metric(data.memory?.percent, "%")}
              </b>
              {!metricsUnavailable && data.memory && (
                <span className="metric-sub">
                  可用 {(data.memory.available_mb / 1024).toFixed(1)} / {(data.memory.total_mb / 1024).toFixed(1)} GB
                </span>
              )}
            </div>
            <div className="metric-card">
              <span className="metric-label">磁盘剩余</span>
              <b className="metric-value">{metric(data.disk_free_gb, " GB")}</b>
            </div>
            <div className="metric-card">
              <span className="metric-label">当日采集量</span>
              <b className="metric-value">{data.articles_today}</b>
              <span className="metric-sub">篇（published_at 当日）</span>
            </div>
            <div className="metric-card">
              <span className="metric-label">队列积压</span>
              <b className="metric-value">{metric(data.queue_backlog_raw_articles)}</b>
              <span className="metric-sub">raw:articles</span>
            </div>
            <div className="metric-card">
              <span className="metric-label">延迟 P95（24h）</span>
              <b className={`metric-value ${(data.latency_p95_min_24h ?? 0) > 30 ? "metric-danger" : ""}`}>
                {metric(data.latency_p95_min_24h, " 分钟")}
              </b>
              <span className="metric-sub">红线 30 分钟</span>
            </div>
            <div className="metric-card">
              <span className="metric-label">活跃议题</span>
              <b className="metric-value">{data.active_topics}</b>
            </div>
            <div className="metric-card">
              <span className="metric-label">用户数</span>
              <b className="metric-value">{data.users}</b>
            </div>
          </div>

          <h4 className="sys-sub-title">分通道延迟 P95（近 24h）</h4>
          <table className="sys-table">
            <thead>
              <tr><th>通道</th><th>P95（分钟）</th><th>样本量</th></tr>
            </thead>
            <tbody>
              {data.latency_by_channel_24h.length === 0 && (
                <tr><td colSpan={3} className="sys-empty">近 24h 无延迟样本</td></tr>
              )}
              {data.latency_by_channel_24h.map((c) => (
                <tr key={c.key}>
                  <td>{c.key}</td>
                  <td className={c.p95_min > 30 ? "cell-danger" : ""}>{c.p95_min}</td>
                  <td>{c.sample}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
