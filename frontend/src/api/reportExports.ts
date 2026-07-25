/** 报告导出 API（详细设计 1.11）。 */
import { request } from "./client";

export type ReportTemplate = "topic_deep" | "compare_brief" | "periodic_weekly";
export type ReportFormat = "pdf" | "docx" | "markdown" | "csv";
export type ReportStatus = "processing" | "done" | "failed";

export interface ReportExportItem {
  id: string;
  template: ReportTemplate;
  format: ReportFormat;
  status: ReportStatus;
  scope_summary: string;
  file_size: number | null;
  duration_ms: number | null;
  created_at: string;
  expires_at: string | null;
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

export interface ReportExportScope {
  topic_id?: string;
  countries?: string[];
  from: string; // YYYY-MM-DD
  to: string;
}

export interface ReportExportPayload {
  template: ReportTemplate;
  format: ReportFormat;
  scope: ReportExportScope;
  locale?: string;
}

export interface ReportExportCreateResult {
  id: string;
  status: ReportStatus;
  download_url?: string;
  queue_position?: number;
  duration_ms?: number;
  pages?: number;
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
