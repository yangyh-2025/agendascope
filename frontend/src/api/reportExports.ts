/**
 * 报告导出 API（T4.17/T4.18）。
 * 契约：POST /api/v1/report-exports {report_type, params, format, time_range}；
 * 列表轮询状态 pending/processing/done/failed；done 后 GET /{id}/download 下载。
 */
import { request } from "./client";

export type ReportType = "topic_deep" | "compare_brief" | "periodic_weekly";
export type ReportFormat = "pdf" | "docx";
export type ReportStatus = "pending" | "processing" | "done" | "failed";

export const REPORT_TYPE_LABEL: Record<ReportType, string> = {
  topic_deep: "议题深度报告",
  compare_brief: "跨国对比简报",
  periodic_weekly: "周期监测周报",
};

export const REPORT_STATUS_LABEL: Record<ReportStatus, string> = {
  pending: "排队中",
  processing: "生成中",
  done: "已完成",
  failed: "失败",
};

export interface ReportExportItem {
  id: string;
  report_type: ReportType;
  format: ReportFormat;
  status: ReportStatus;
  /** 失败原因（status=failed 时）。 */
  error?: string | null;
  scope_summary?: string;
  file_size?: number | null;
  duration_ms?: number | null;
  created_at: string;
  expires_at?: string | null;
}

export interface ReportExportListPage {
  total: number;
  page: number;
  page_size: number;
  items: ReportExportItem[];
}

export function listReportExports(params: { page?: number; page_size?: number } = {}) {
  const qs = new URLSearchParams({
    page: String(params.page ?? 1),
    page_size: String(params.page_size ?? 20),
  });
  return request<ReportExportListPage>(`/api/v1/report-exports?${qs.toString()}`);
}

export interface ReportTimeRange {
  from: string; // YYYY-MM-DD
  to: string; // YYYY-MM-DD
}

export interface ReportExportPayload {
  report_type: ReportType;
  format: ReportFormat;
  time_range: ReportTimeRange;
  /** 模板参数：topic_deep 需 topic_id；compare_brief 需 countries（2–4 国）。 */
  params?: {
    topic_id?: string;
    countries?: string[];
    [key: string]: unknown;
  };
}

export interface ReportExportCreateResult {
  id: string;
  status: ReportStatus;
  queue_position?: number;
}

export function createReportExport(payload: ReportExportPayload) {
  return request<ReportExportCreateResult>("/api/v1/report-exports", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function reportDownloadUrl(id: string): string {
  return `/api/v1/report-exports/${encodeURIComponent(id)}/download`;
}
