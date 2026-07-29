/**
 * 系统管理后台 API（T5.10/T5.13）：概览/用户/审计日志/日志查看/许可/诊断包。
 * 全部端点管理员限定（非 admin 由后端 403 拦截，页面层也按角色隐藏）。
 */
import { ApiError, getStoredTokens, request } from "./client";

export interface OverviewMetrics {
  cpu: { cores: number; percent: number } | null;
  memory: { total_mb: number; available_mb: number; percent: number } | null;
  /** "unavailable" 时 cpu/memory 为 null，页面显示"指标不可用"而非 0。 */
  metrics_status: "ok" | "unavailable";
  disk_free_gb: number | null;
  articles_today: number;
  active_topics: number;
  users: number;
  queue_backlog_raw_articles: number | null;
  latency_p95_min_24h: number | null;
  latency_by_channel_24h: { key: string; p95_min: number; sample: number }[];
}

export interface AdminUserItem {
  id: string;
  username: string;
  display_name: string;
  role: string;
  status: string;
  created_at: string | null;
}

export interface AuditLogItem {
  id: string;
  at: string | null;
  username: string | null;
  action: string;
  resource: string | null;
  detail: unknown;
  ip: string | null;
  user_agent: string | null;
  result: string;
}

export interface AuditLogPage {
  items: AuditLogItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface AuditLogFilters {
  start?: string;
  end?: string;
  actor?: string;
  action?: string;
  result?: "success" | "failure" | "denied" | "";
}

export type LogLevel = "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL";

export interface LogTail {
  items: string[];
  matched: number;
  truncated: boolean;
  level: LogLevel;
  log_file: string;
}

export type LicenseStatus = "community" | "active" | "expired";
export type LicenseReminder = "none" | "30d" | "7d" | "1d" | "expired";

export interface LicenseInfo {
  status: LicenseStatus;
  license_id: string | null;
  product: string | null;
  expires_at: string | null;
  days_remaining: number | null;
  reminder_level: LicenseReminder;
  write_allowed: boolean;
  activated_at?: string | null;
  note?: string;
}

export function fetchSystemOverview(): Promise<OverviewMetrics> {
  return request<OverviewMetrics>("/api/v1/system/overview");
}

export function listSystemUsers(): Promise<{ items: AdminUserItem[] }> {
  return request<{ items: AdminUserItem[] }>("/api/v1/system/users");
}

export function updateUserRole(userId: string, role: string): Promise<{ id: string; role: string }> {
  return request<{ id: string; role: string }>(
    `/api/v1/system/users/${encodeURIComponent(userId)}/role`,
    { method: "PATCH", body: JSON.stringify({ role }) },
  );
}

function auditQuery(filters: AuditLogFilters): URLSearchParams {
  const qs = new URLSearchParams();
  if (filters.start) qs.set("start", filters.start);
  if (filters.end) qs.set("end", filters.end);
  if (filters.actor) qs.set("actor", filters.actor);
  if (filters.action) qs.set("action", filters.action);
  if (filters.result) qs.set("result", filters.result);
  return qs;
}

export function listAuditLogs(
  filters: AuditLogFilters,
  page = 1,
  pageSize = 20,
): Promise<AuditLogPage> {
  const qs = auditQuery(filters);
  qs.set("page", String(page));
  qs.set("page_size", String(pageSize));
  return request<AuditLogPage>(`/api/v1/system/audit-logs?${qs.toString()}`);
}

export function fetchSystemLogs(level: LogLevel, lines: number): Promise<LogTail> {
  const qs = new URLSearchParams({ level, lines: String(lines) });
  return request<LogTail>(`/api/v1/system/logs?${qs.toString()}`);
}

export function fetchLicense(): Promise<LicenseInfo> {
  return request<LicenseInfo>("/api/v1/system/license");
}

export function enrollLicense(code: string): Promise<LicenseInfo> {
  return request<LicenseInfo>("/api/v1/system/license", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

/**
 * 鉴权文件下载：CSV 导出与诊断包端点要求 admin token，不能走裸 <a href>。
 * 成功时从 Content-Disposition 取文件名触发浏览器保存；失败解析统一错误信封抛出。
 */
async function downloadAuthedFile(
  path: string,
  init: RequestInit,
  fallbackName: string,
): Promise<void> {
  const tokens = getStoredTokens();
  const headers = new Headers(init.headers);
  if (tokens) headers.set("Authorization", `Bearer ${tokens.access_token}`);

  let res: Response;
  try {
    res = await fetch(path, { ...init, headers });
  } catch {
    throw new ApiError(-1, "网络异常，请稍后重试", 0);
  }
  if (!res.ok) {
    let message = `下载失败（HTTP ${res.status}）`;
    let code = -1;
    try {
      const envelope = (await res.json()) as { code?: number; message?: string };
      if (typeof envelope.code === "number") code = envelope.code;
      if (envelope.message) message = envelope.message;
    } catch {
      /* 非 JSON 错误响应，沿用默认信息 */
    }
    throw new ApiError(code, message, res.status);
  }

  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^";]+)"?/.exec(disposition);
  const filename = match ? match[1].trim() : fallbackName;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** 审计日志 CSV 导出（沿用当前过滤条件，上限 10000 行由后端控制）。 */
export function exportAuditLogs(filters: AuditLogFilters): Promise<void> {
  const qs = auditQuery(filters);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return downloadAuthedFile(`/api/v1/system/audit-logs/export${suffix}`, {}, "audit_logs.csv");
}

/** 一键诊断包 zip 下载（T5.13）。 */
export function downloadDiagnostics(): Promise<void> {
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "");
  return downloadAuthedFile("/api/v1/system/diagnostics", { method: "POST" }, `diagnostics_${stamp}.zip`);
}
