/** 议程设置事件列表页:跨国传播事件独立入口(与议题列表区分)。 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import {
  listAgendaEvents,
  AgendaEventListItem,
  AgendaEventStatus,
} from "../api/agendaEvents";
import { COUNTRIES, AGENDA_EVENT_STATUS_LABEL, countryLabel } from "../api/meta";
import "./EventsPage.css";

const STATUS_OPTIONS: { value: AgendaEventStatus | ""; label: string }[] = [
  { value: "", label: "全部状态" },
  { value: "watching", label: "观察中" },
  { value: "suspected", label: "疑似" },
  { value: "confirmed", label: "已确认" },
  { value: "dismissed", label: "已排除" },
  { value: "revised", label: "已修正" },
  { value: "archived", label: "已归档" },
];

const SORT_OPTIONS = [
  { value: "origin_at_desc", label: "首发时间 ↓" },
  { value: "origin_at_asc", label: "首发时间 ↑" },
  { value: "follower_count_desc", label: "跟随国数" },
  { value: "updated_at_desc", label: "最近更新" },
];

export default function EventsPage() {
  const nav = useNavigate();
  const [items, setItems] = useState<AgendaEventListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<AgendaEventStatus | "">("");
  const [country, setCountry] = useState("");
  const [sort, setSort] = useState("origin_at_desc");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetch = useCallback(() => {
    setError(null);
    setLoading(true);
    listAgendaEvents({
      status,
      origin_country_code: country || undefined,
      sort,
      page,
      page_size: 20,
    })
      .then((r) => {
        setItems(r.items);
        setTotal(r.total);
      })
      .catch((err) => {
        setError(err instanceof ApiError ? err.message : "议程事件加载失败,请稍后重试");
      })
      .finally(() => setLoading(false));
  }, [status, country, sort, page]);

  useEffect(() => {
    fetch();
  }, [fetch]);

  return (
    <div className="events-page">
      <header className="page-header">
        <div>
          <h1 className="page-title">议程设置事件</h1>
          <p className="page-desc">
            满足"首发源明确 + ≥3 国跟随 + 统计显著"的跨国传播事件。点击事件查看传播链路、检验证据与修正留痕。
          </p>
        </div>
      </header>

      <div className="filter-bar">
        <select value={status} onChange={(e) => { setStatus(e.target.value as AgendaEventStatus | ""); setPage(1); }}>
          {STATUS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <select value={country} onChange={(e) => { setCountry(e.target.value); setPage(1); }}>
          <option value="">全部首发国</option>
          {COUNTRIES.map((c) => <option key={c.code} value={c.code}>{c.label}</option>)}
        </select>
        <select value={sort} onChange={(e) => { setSort(e.target.value); setPage(1); }}>
          {SORT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </div>

      {error && <p className="page-error" role="alert">{error}</p>}

      <div className="event-grid">
        {items.map((ev) => (
          <div
            key={ev.id}
            className="event-card clickable"
            role="button"
            tabIndex={0}
            onClick={() => nav(`/events/${ev.id}`)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                nav(`/events/${ev.id}`);
              }
            }}
            title={`查看事件详情(关联议题:${ev.topic_name})`}
          >
            <div className="event-card-header">
              <h3>{ev.topic_name}</h3>
              <span className={`event-status-tag status-${ev.status}`}>
                {AGENDA_EVENT_STATUS_LABEL[ev.status] ?? ev.status}
              </span>
            </div>
            <p className="event-summary">
              <span className="origin-country">{countryLabel(ev.origin_country_code)}</span>
              {" "}首发 · {ev.follower_count} 国跟随
              {ev.max_lag_hours != null && ` · 最长时滞 ${ev.max_lag_hours.toFixed(1)}h`}
            </p>
            <div className="event-card-footer">
              <span className="origin-label">{ev.origin_label}</span>
              {ev.final_review && (
                <span className={`review-tag verdict-${ev.final_review.verdict}`}>
                  终审 {ev.final_review.score.toFixed(2)}
                </span>
              )}
              {ev.confirmed_by ? (
                <span className="confirm-tag manual">人工确认</span>
              ) : ev.status === "confirmed" ? (
                <span className="confirm-tag auto">LLM 确认</span>
              ) : null}
            </div>
          </div>
        ))}
      </div>

      {loading && !error && <p className="page-loading">加载中…</p>}
      {items.length === 0 && !error && !loading && (
        <div className="empty-state">
          <p>暂无满足条件的议程事件。事件需同时满足首发源明确、≥3 国跟随、统计显著三个条件。</p>
        </div>
      )}

      <div className="pagination">
        <button className="as-btn-ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>← 上一页</button>
        <span className="pagination-info">第 {page} / {Math.max(1, Math.ceil(total / 20))} 页 · 共 {total} 条</span>
        <button className="as-btn-ghost" disabled={page * 20 >= total} onClick={() => setPage(page + 1)}>下一页 →</button>
      </div>
    </div>
  );
}
