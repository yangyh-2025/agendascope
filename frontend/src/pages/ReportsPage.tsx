/** 报告中心页（T4.18）：三模板导出 + 列表轮询状态 + 完成后下载。 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { COUNTRIES } from "../api/meta";
import {
  createReportExport,
  listReportExports,
  REPORT_STATUS_LABEL,
  REPORT_TYPE_LABEL,
  reportDownloadUrl,
  type ReportFormat,
  type ReportExportItem,
  type ReportType,
} from "../api/reportExports";
import { listTopics, type TopicListItem } from "../api/topics";
import "./ReportsPage.css";

const MAX_RANGE_DAYS = 90;
const POLL_INTERVAL_MS = 5000;

function fmtDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

export default function ReportsPage() {
  const today = new Date();
  const [reportType, setReportType] = useState<ReportType>("topic_deep");
  const [format, setFormat] = useState<ReportFormat>("pdf");
  const [from, setFrom] = useState(fmtDate(new Date(today.getTime() - 6 * 86400_000)));
  const [to, setTo] = useState(fmtDate(today));
  const [topicId, setTopicId] = useState("");
  const [countries, setCountries] = useState<string[]>(["CN", "US"]);
  const [topics, setTopics] = useState<TopicListItem[]>([]);

  const [items, setItems] = useState<ReportExportItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitMsg, setSubmitMsg] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(() => {
    listReportExports({ page: 1, page_size: 20 })
      .then((r) => setItems(r.items))
      .catch((err) => setError(errMsg(err, "报告列表加载失败")));
  }, []);

  // 初始加载 + 有待处理任务时轮询
  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const hasActive = items.some((it) => it.status === "pending" || it.status === "processing");
    if (hasActive && !timerRef.current) {
      timerRef.current = setInterval(refresh, POLL_INTERVAL_MS);
    } else if (!hasActive && timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, [items, refresh]);

  useEffect(
    () => () => {
      if (timerRef.current) clearInterval(timerRef.current);
    },
    [],
  );

  // 议题深度报告需要选择议题
  useEffect(() => {
    if (reportType !== "topic_deep") return;
    listTopics({ sort: "salience", page: 1, page_size: 50 })
      .then((r) => {
        setTopics(r.items);
        setTopicId((prev) => prev || r.items[0]?.id || "");
      })
      .catch(() => setTopics([]));
  }, [reportType]);

  const toggleCountry = (code: string) => {
    setCountries((prev) =>
      prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code].slice(0, 4),
    );
  };

  const rangeDays = (new Date(to).getTime() - new Date(from).getTime()) / 86400_000;

  const submit = () => {
    setSubmitMsg(null);
    setError(null);
    if (rangeDays < 0 || rangeDays > MAX_RANGE_DAYS) {
      setError(`时间窗上限 ${MAX_RANGE_DAYS} 天，请调整起止日期`);
      return;
    }
    if (reportType === "topic_deep" && !topicId) {
      setError("议题深度报告需要选择一个议题");
      return;
    }
    if (reportType === "compare_brief" && (countries.length < 2 || countries.length > 4)) {
      setError("跨国对比简报需要选择 2–4 个国家");
      return;
    }
    setSubmitting(true);
    createReportExport({
      report_type: reportType,
      format,
      time_range: { from, to },
      params:
        reportType === "topic_deep"
          ? { topic_id: topicId }
          : reportType === "compare_brief"
            ? { countries }
            : {},
    })
      .then((r) => {
        setSubmitMsg(`已创建导出任务（${r.status === "pending" ? "排队中" : "生成中"}），完成后可下载`);
        refresh();
      })
      .catch((err) => setError(errMsg(err, "报告创建失败")))
      .finally(() => setSubmitting(false));
  };

  return (
    <div className="reports-page">
      <header className="page-header">
        <div>
          <h1 className="page-title">报告中心</h1>
          <p className="page-desc">生成议题深度报告、跨国对比简报、周期摘要,支持 PDF 与 Word 格式。</p>
        </div>
      </header>
      <div className="report-form">
        <label>
          报告类型
          <select value={reportType} onChange={(e) => setReportType(e.target.value as ReportType)}>
            {(Object.entries(REPORT_TYPE_LABEL) as [ReportType, string][]).map(([v, label]) => (
              <option key={v} value={v}>{label}</option>
            ))}
          </select>
        </label>
        <label>
          格式
          <select value={format} onChange={(e) => setFormat(e.target.value as ReportFormat)}>
            <option value="pdf">PDF</option>
            <option value="docx">Word（docx）</option>
          </select>
        </label>
        <div className="time-range">
          <label>
            起始日期
            <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          </label>
          <label>
            截止日期
            <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          </label>
        </div>
        {reportType === "topic_deep" && (
          <label>
            议题
            <select value={topicId} onChange={(e) => setTopicId(e.target.value)}>
              {topics.length === 0 && <option value="">暂无可选议题</option>}
              {topics.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
          </label>
        )}
        {reportType === "compare_brief" && (
          <div className="country-picker">
            <span className="picker-label">对比国家（2–4 个）</span>
            <div className="country-chips">
              {COUNTRIES.slice(0, 15).map((c) => (
                <button
                  key={c.code}
                  type="button"
                  className={`country-chip ${countries.includes(c.code) ? "active" : ""}`}
                  onClick={() => toggleCountry(c.code)}
                >
                  {c.label}
                </button>
              ))}
            </div>
          </div>
        )}
        <span className="watermark-hint">
          报告含水印“由 AgendaScope 观澜生成 + 数据口径声明”；不含全文，仅标题与摘录；时间窗上限 90 天。
        </span>
        <button disabled={submitting} onClick={submit}>
          {submitting ? "提交中…" : "生成报告"}
        </button>
        {submitMsg && <p className="status-msg">{submitMsg}</p>}
        {error && <p className="page-error" role="alert">{error}</p>}
      </div>

      <h2 className="export-list-title">导出记录</h2>
      <table className="export-table">
        <thead>
          <tr>
            <th>类型</th>
            <th>格式</th>
            <th>状态</th>
            <th>创建时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 && (
            <tr><td colSpan={5} className="export-empty">暂无导出记录</td></tr>
          )}
          {items.map((it) => (
            <tr key={it.id}>
              <td>{REPORT_TYPE_LABEL[it.report_type] ?? it.report_type}</td>
              <td>{it.format.toUpperCase()}</td>
              <td>
                <span className={`export-status export-status-${it.status}`}>
                  {REPORT_STATUS_LABEL[it.status] ?? it.status}
                </span>
                {it.status === "failed" && it.error && (
                  <span className="export-error">{it.error}</span>
                )}
              </td>
              <td>{it.created_at?.slice(0, 16).replace("T", " ")}</td>
              <td>
                {it.status === "done" ? (
                  <a className="download-link" href={reportDownloadUrl(it.id)}>下载</a>
                ) : (
                  <span className="download-pending">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
