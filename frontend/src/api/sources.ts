import { request } from "./client";

export type SourceStatus = "active" | "degraded" | "failed";
export type MediaType = "newspaper" | "agency" | "broadcast" | "online";

export interface SourceHealth24h {
  success_rate: number | null;
  articles_24h: number;
  avg_latency_min: number | null;
}

export interface SourceListItem {
  id: string;
  name: string;
  name_zh: string | null;
  country_code: string;
  media_type: MediaType;
  language: string;
  collect_mode: string;
  adapter_type: string;
  poll_interval_min: number;
  audience_weight: number | null;
  coverage_confidence: string;
  status: SourceStatus;
  is_custom: boolean;
  last_success_at: string | null;
  health_24h: SourceHealth24h;
}

export interface SourceListPage {
  total: number;
  page: number;
  page_size: number;
  items: SourceListItem[];
}

export interface ListSourcesParams {
  country_code?: string;
  status?: SourceStatus;
  keyword?: string;
  page?: number;
  page_size?: number;
}

export function listSources(params: ListSourcesParams = {}): Promise<SourceListPage> {
  const qs = new URLSearchParams();
  if (params.country_code) qs.set("country_code", params.country_code);
  if (params.status) qs.set("status", params.status);
  if (params.keyword) qs.set("keyword", params.keyword);
  qs.set("page", String(params.page ?? 1));
  qs.set("page_size", String(params.page_size ?? 20));
  return request<SourceListPage>(`/api/v1/sources?${qs.toString()}`);
}

export interface PreviewSample {
  url: string;
  title: string | null;
  authors: string | null;
  pub_time: string | null;
  content_len: number;
  ok: boolean;
}

export interface CrawlPreviewResult {
  adapter_type: "rss" | "pipeline";
  resolved_config: Record<string, unknown> & { entry_points?: string[] };
  discovered: Record<string, unknown>;
  samples: PreviewSample[];
  warnings: string[];
  elapsed_ms: number;
}

export function crawlPreview(url: string): Promise<CrawlPreviewResult> {
  return request<CrawlPreviewResult>("/api/v1/sources/crawl-preview", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
}

export interface SourceCreatePayload {
  name: string;
  name_zh?: string;
  country_code: string;
  homepage_url: string;
  feed_url?: string;
  collect_mode: "rss" | "rsshub" | "gdelt";
  adapter_type: "rss" | "pipeline";
  crawl_config?: Record<string, unknown>;
  media_type: MediaType;
  language: string;
  poll_interval_min?: number;
  coverage_confidence?: "high" | "medium" | "low";
}

export function createSource(
  payload: SourceCreatePayload,
): Promise<{ id: string; status: string }> {
  return request<{ id: string; status: string }>("/api/v1/sources", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
