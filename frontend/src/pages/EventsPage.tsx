/** 议程设置事件列表页（T4.9）。 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { request, ApiError } from "../api/client";
import { COUNTRIES, AGENDA_EVENT_STATUS_LABEL } from "../api/meta";
import "./EventsPage.css";

const STATUS_COLORS: Record<string, string> = { confirmed: "#C8102E", suspected: "#D97706", watching: "#64748B", revised: "#1D4E9E", dismissed: "#9CA3AF", archived: "#374151" };

interface EventItem { id: string; topic_id: string; status: string; confidence: string; origin_type: string; origin_country_code: string; origin_at: string; origin_confidence: string; follower_count: number; stats_significant: boolean | null; detection_method: string; final_review_score: number | null; updated_at: string; }

export default function EventsPage() {
  const nav = useNavigate();
  const [items, setItems] = useState<EventItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [origin, setOrigin] = useState("");
  const [error, setError] = useState<string | null>(null);

  const fetch = useCallback(() => {
    setError(null);
    const qs = new URLSearchParams();
    if (status) qs.set("status", status);
    if (origin) qs.set("origin_country_code", origin);
    qs.set("page", String(page));
    qs.set("page_size", "20");
    request<{ items: EventItem[]; total: number }>(`/api/v1/agenda-events?${qs}`).then((r) => {
      setItems(r.items); setTotal(r.total);
    }).catch((err) => {
      setError(err instanceof ApiError ? err.message : "事件列表加载失败，请稍后重试");
    });
  }, [status, origin, page]);

  useEffect(() => { fetch(); }, [fetch]);

  return (
    <div className="events-page">
      <h1>议程设置事件</h1>
      <div className="filters">
        <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">全部状态</option>
          {Object.entries(AGENDA_EVENT_STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select value={origin} onChange={(e) => { setOrigin(e.target.value); setPage(1); }}>
          <option value="">全部首发国</option>
          {COUNTRIES.map((c) => <option key={c.code} value={c.code}>{c.label}</option>)}
        </select>
      </div>
      {error && <p className="page-error" role="alert">{error}</p>}
      <div className="event-list">
        {items.map((ev) => (
          <div key={ev.id} className="event-card" onClick={() => nav(`/events/${ev.id}`)}>
            <span className="event-status" style={{ background: STATUS_COLORS[ev.status] || "#374151" }}>{AGENDA_EVENT_STATUS_LABEL[ev.status]}</span>
            <div className="event-body">
              <div className="event-origin">{ev.origin_country_code} → 跟随 {ev.follower_count} 国</div>
              <div className="event-ts">{ev.origin_at?.slice(0, 10)} · {ev.detection_method}</div>
            </div>
            <div className="event-score">{ev.final_review_score != null ? `终审 ${ev.final_review_score}/10` : "待终审"}</div>
          </div>
        ))}
      </div>
      <div className="pagination">
        <button disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
        <span>{page} / {Math.ceil(total / 20)}（共 {total} 条）</span>
        <button disabled={page * 20 >= total} onClick={() => setPage(page + 1)}>下一页</button>
      </div>
    </div>
  );
}
