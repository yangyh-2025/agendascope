/** 审计日志面板：时间/操作人/动作/结果过滤 + 分页表格 + CSV 导出（沿用当前过滤条件）。 */
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../../api/client";
import {
  exportAuditLogs,
  listAuditLogs,
  type AuditLogFilters,
  type AuditLogPage,
} from "../../api/systemAdmin";

const PAGE_SIZE = 20;
const RESULT_OPTIONS: { value: AuditLogFilters["result"]; label: string }[] = [
  { value: "", label: "全部结果" },
  { value: "success", label: "success" },
  { value: "failure", label: "failure" },
  { value: "denied", label: "denied" },
];

function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** datetime-local 值转 ISO8601（后端按含端点过滤）；空值返回 undefined。 */
function toIso(local: string): string | undefined {
  if (!local) return undefined;
  const d = new Date(local);
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}

export default function AuditLogsPanel() {
  // 输入中的过滤条件（点击“查询”后才生效）
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const [result, setResult] = useState<AuditLogFilters["result"]>("");
  // 已生效的过滤条件与分页
  const [applied, setApplied] = useState<AuditLogFilters>({});
  const [page, setPage] = useState(1);
  const [data, setData] = useState<AuditLogPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const load = useCallback((filters: AuditLogFilters, p: number) => {
    setError(null);
    listAuditLogs(filters, p, PAGE_SIZE)
      .then(setData)
      .catch((err) => setError(err instanceof ApiError ? err.message : "审计日志加载失败"));
  }, []);

  useEffect(() => {
    load(applied, page);
  }, [applied, page, load]);

  const applyFilters = () => {
    const next: AuditLogFilters = {
      start: toIso(start),
      end: toIso(end),
      actor: actor.trim() || undefined,
      action: action.trim() || undefined,
      result: result || undefined,
    };
    setPage(1);
    setApplied(next);
  };

  const doExport = () => {
    setError(null);
    setExporting(true);
    exportAuditLogs(applied)
      .catch((err) => setError(err instanceof ApiError ? err.message : "审计日志导出失败"))
      .finally(() => setExporting(false));
  };

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <section className="sys-panel">
      <div className="sys-panel-head">
        <h3>审计日志</h3>
        <button type="button" className="as-btn-ghost" disabled={exporting} onClick={doExport}>
          {exporting ? "导出中…" : "导出 CSV"}
        </button>
      </div>

      <div className="sys-filters">
        <label>
          <span>起始时间</span>
          <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label>
          <span>截止时间</span>
          <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
        <label>
          <span>操作人</span>
          <input value={actor} onChange={(e) => setActor(e.target.value)} placeholder="用户名" maxLength={64} />
        </label>
        <label>
          <span>动作</span>
          <input value={action} onChange={(e) => setAction(e.target.value)} placeholder="如 auth.login" maxLength={50} />
        </label>
        <label>
          <span>结果</span>
          <select value={result} onChange={(e) => setResult(e.target.value as AuditLogFilters["result"])}>
            {RESULT_OPTIONS.map((o) => (
              <option key={o.label} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
        <button type="button" onClick={applyFilters}>查询</button>
      </div>

      {error && <p className="page-error" role="alert">{error}</p>}
      {!error && !data && <p className="page-loading">加载中…</p>}

      {data && (
        <>
          <table className="sys-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>操作人</th>
                <th>动作</th>
                <th>资源</th>
                <th>结果</th>
                <th>IP</th>
              </tr>
            </thead>
            <tbody>
              {data.items.length === 0 && (
                <tr><td colSpan={6} className="sys-empty">无匹配审计记录</td></tr>
              )}
              {data.items.map((item) => (
                <tr key={item.id}>
                  <td>{formatTime(item.at)}</td>
                  <td>{item.username || "—"}</td>
                  <td>{item.action}</td>
                  <td>{item.resource || "—"}</td>
                  <td className={item.result === "success" ? "" : "cell-danger"}>{item.result}</td>
                  <td>{item.ip || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="sys-pager">
            <button type="button" className="as-btn-ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              上一页
            </button>
            <span>第 {data.page} / {totalPages} 页 · 共 {data.total} 条</span>
            <button
              type="button"
              className="as-btn-ghost"
              disabled={page >= totalPages}
              onClick={() => setPage(page + 1)}
            >
              下一页
            </button>
          </div>
        </>
      )}
    </section>
  );
}
