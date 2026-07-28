/** 修正历史页（T4.12）：revision_log 列表 + 人工确认/否决/误并回滚入口。 */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  confirmAgendaEvent,
  dismissAgendaEvent,
  listAgendaEventRevisions,
  listAgendaEvents,
  rejectRevision,
  type AgendaEventListItem,
  type RevisionLogItem,
} from "../api/agendaEvents";
import { ApiError } from "../api/client";
import { AGENDA_EVENT_STATUS_LABEL, countryLabel } from "../api/meta";
import "./RevisionsPage.css";

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

interface RejectingState {
  eventId: string;
  seq: number;
  reason: string;
}

export default function RevisionsPage() {
  const [events, setEvents] = useState<AgendaEventListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [revisions, setRevisions] = useState<Record<string, RevisionLogItem[]>>({});
  const [rejecting, setRejecting] = useState<RejectingState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  const loadEvents = useCallback(() => {
    listAgendaEvents({ page, page_size: 20, sort: "updated_at" })
      .then((r) => {
        setEvents(r.items);
        setTotal(r.total);
      })
      .catch((err) => setError(errMsg(err, "事件列表加载失败")));
  }, [page]);

  useEffect(() => {
    loadEvents();
  }, [loadEvents]);

  const loadRevisions = useCallback((eventId: string) => {
    listAgendaEventRevisions(eventId)
      .then((r) => setRevisions((prev) => ({ ...prev, [eventId]: r.revisions })))
      .catch((err) => setError(errMsg(err, "修正记录加载失败")));
  }, []);

  const toggle = (eventId: string) => {
    if (expandedId === eventId) {
      setExpandedId(null);
      return;
    }
    setExpandedId(eventId);
    if (!revisions[eventId]) loadRevisions(eventId);
  };

  const refreshEvent = (eventId: string) => {
    loadEvents();
    loadRevisions(eventId);
  };

  const handleConfirm = (eventId: string) => {
    setActionMsg(null);
    confirmAgendaEvent(eventId)
      .then(() => {
        setActionMsg("已人工确认该事件");
        refreshEvent(eventId);
      })
      .catch((err) => setActionMsg(errMsg(err, "确认失败")));
  };

  const handleDismiss = (eventId: string) => {
    setActionMsg(null);
    dismissAgendaEvent(eventId, { reason: "人工排除", false_positive: true })
      .then(() => {
        setActionMsg("已排除该事件（标记误报）");
        refreshEvent(eventId);
      })
      .catch((err) => setActionMsg(errMsg(err, "排除失败")));
  };

  const handleReject = () => {
    if (!rejecting || !rejecting.reason.trim()) return;
    setActionMsg(null);
    rejectRevision(rejecting.eventId, rejecting.seq, rejecting.reason.trim())
      .then(() => {
        setActionMsg(`已否决第 ${rejecting.seq} 条修正`);
        setRejecting(null);
        refreshEvent(rejecting.eventId);
      })
      .catch((err) => setActionMsg(errMsg(err, "否决失败")));
  };

  return (
    <div className="revisions-page">
      <h1>修正历史</h1>
      <p className="page-desc">
        事件级修正留痕（revision_log）与人工处置入口；议题误并回滚请在
        <Link to="/topics">议题详情页</Link>“已并入的源议题”处操作。
      </p>

      {error && <p className="page-error" role="alert">{error}</p>}
      {actionMsg && <p className="status-msg">{actionMsg}</p>}

      <div className="revision-event-list">
        {events.length === 0 && !error && <p className="page-loading">暂无事件</p>}
        {events.map((ev) => {
          const expanded = expandedId === ev.id;
          const revs = revisions[ev.id] ?? [];
          return (
            <div key={ev.id} className="revision-event-card">
              <button className="event-head" onClick={() => toggle(ev.id)}>
                <span className={`event-status st-${ev.status}`}>{AGENDA_EVENT_STATUS_LABEL[ev.status] ?? ev.status}</span>
                <span className="event-topic">{ev.topic_name ?? ev.topic_id}</span>
                <span className="event-origin">首发 {countryLabel(ev.origin_country_code)}</span>
                <span className="event-rev-count">
                  {ev.latest_revision_at ? `最近修正 ${ev.latest_revision_at.slice(0, 16).replace("T", " ")}` : "无修正记录"}
                  {" "}{expanded ? "▲" : "▼"}
                </span>
              </button>
              {expanded && (
                <div className="event-body">
                  <div className="event-actions">
                    <Link className="detail-link" to={`/events/${ev.id}`}>查看详情</Link>
                    {ev.status !== "confirmed" && (
                      <button onClick={() => handleConfirm(ev.id)}>人工确认</button>
                    )}
                    {ev.status !== "dismissed" && (
                      <button className="as-btn-danger" onClick={() => handleDismiss(ev.id)}>排除（误报）</button>
                    )}
                  </div>
                  {revs.length === 0 && <p className="drawer-empty">该事件暂无修正记录</p>}
                  {revs.map((r) => (
                    <div key={r.seq} className={`revision-item ${r.rejected ? "rejected" : ""}`}>
                      <div className="revision-main">
                        <span className="revision-field">#{r.seq} {r.field}</span>
                        <span className="revision-change">{String(r.before_value)} → {String(r.after_value)}</span>
                        <span className="revision-meta">
                          {r.actor === "human" ? "人工" : `机器${r.model ? `（${r.model}）` : ""}`} · {r.revised_at?.slice(0, 16).replace("T", " ")}
                          {r.rejected && " · 已否决"}
                        </span>
                      </div>
                      {!r.rejected && (
                        <button
                          className="as-btn-ghost"
                          onClick={() => setRejecting({ eventId: ev.id, seq: r.seq, reason: "" })}
                        >
                          否决
                        </button>
                      )}
                    </div>
                  ))}
                  {rejecting && rejecting.eventId === ev.id && (
                    <div className="reject-form">
                      <input
                        placeholder="否决原因（必填）"
                        value={rejecting.reason}
                        onChange={(e) => setRejecting({ ...rejecting, reason: e.target.value })}
                      />
                      <button disabled={!rejecting.reason.trim()} onClick={handleReject}>提交否决</button>
                      <button className="as-btn-ghost" onClick={() => setRejecting(null)}>取消</button>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="pagination">
        <button disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
        <span>{page} / {Math.max(Math.ceil(total / 20), 1)}（共 {total} 条）</span>
        <button disabled={page * 20 >= total} onClick={() => setPage(page + 1)}>下一页</button>
      </div>
    </div>
  );
}
