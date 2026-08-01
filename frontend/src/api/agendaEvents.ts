/**
 * 议程设置事件（agenda_events）相关 API 调用。
 * 字段命名对齐后端 1.8 节。
 */
import { request } from "./client";

export type AgendaEventStatus =
  | "watching"
  | "suspected"
  | "confirmed"
  | "dismissed"
  | "revised"
  | "archived";

export interface AgendaEventListItem {
  id: string;
  topic_id: string;
  topic_name: string;
  status: AgendaEventStatus;
  confidence: string;
  origin_type: string;
  origin_country_code: string;
  origin_label: string;
  origin_at: string;
  follower_count: number;
  max_lag_hours: number;
  final_review: { score: number; verdict: string } | null;
  latest_revision_at: string | null;
  confirmed_by: string | null;
  confirmed_at: string | null;
  created_at: string;
  updated_at: string;
  detection_method?: string;
}

export interface AgendaEventListPage {
  total: number;
  page: number;
  page_size: number;
  items: AgendaEventListItem[];
}

export interface ListAgendaEventsParams {
  status?: AgendaEventStatus | "";
  origin_country_code?: string;
  topic_id?: string;
  date_from?: string;
  date_to?: string;
  sort?: string;
  page?: number;
  page_size?: number;
}

export function listAgendaEvents(
  params: ListAgendaEventsParams = {},
): Promise<AgendaEventListPage> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.origin_country_code) qs.set("origin_country_code", params.origin_country_code);
  if (params.topic_id) qs.set("topic_id", params.topic_id);
  if (params.date_from) qs.set("from", params.date_from);
  if (params.date_to) qs.set("to", params.date_to);
  if (params.sort) qs.set("sort", params.sort);
  qs.set("page", String(params.page ?? 1));
  qs.set("page_size", String(params.page_size ?? 20));
  return request<AgendaEventListPage>(`/api/v1/agenda-events?${qs.toString()}`);
}

export interface FollowerStep {
  country_code: string;
  first_media?: string;
  /** 后端 follower_sequence 元素实际键名为 first_media_name。 */
  first_media_name?: string;
  first_article_id?: string;
  first_published_at?: string;
  lag_hours: number;
}

export interface StatsEvidence {
  xcorr: { best_lag_days: number; r: number; p: number };
  granger: { p: number; direction: string; significant: boolean };
  qap: { r: number; p: number };
  sample_size: number;
  disclaimer: string;
}

export interface RevisionLogItem {
  seq: number;
  revised_at: string;
  field: string;
  before_value: string;
  after_value: string;
  trigger_evidence: Record<string, unknown> | null;
  actor: "machine" | "human";
  model: string | null;
  prompt_version: string | null;
  rejected: boolean;
}

export interface AgendaEventDetail {
  id: string;
  topic_id: string;
  topic_name: string;
  status: AgendaEventStatus;
  confidence: string;
  origin_type: string;
  origin_country_code: string;
  origin_source: { id: string; name: string; country_code: string } | null;
  origin_entity: { id: string; name: string } | null;
  origin_at: string;
  origin_confidence: string;
  origin_quote: string | null;
  follower_sequence: FollowerStep[];
  stats_evidence: StatsEvidence | null;
  detection_method: string;
  final_review: {
    score: number;
    verdict: string;
    model?: string;
    prompt_version?: string;
    reasoning?: string;
    concerns?: string[];
  } | null;
  judgement_basis: string | null;
  round_no: number;
  revision_log: RevisionLogItem[];
  confirmed_by: string | null;
  confirmed_at: string | null;
  created_at: string;
  updated_at: string;
}

export function getAgendaEvent(id: string): Promise<AgendaEventDetail> {
  return request<AgendaEventDetail>(`/api/v1/agenda-events/${encodeURIComponent(id)}`);
}

export interface AgendaEventChainFollower {
  country: string;
  /** 首发媒体名（后端取自 follower_sequence.first_media_name，非 source_id）。 */
  first_media: string | null;
  first_article_id: string | null;
  first_published_at: string | null;
  lag_hours: number;
}

export interface AgendaEventChainEdge {
  from_country: string;
  to_country: string;
  lag_hours: number;
}

export interface AgendaEventChain {
  event_id: string;
  topic_id: string;
  origin: {
    country: string;
    media: { id: string; name: string } | null;
    published_at: string | null;
    confidence: string;
  };
  follower_sequence: AgendaEventChainFollower[];
  edges: AgendaEventChainEdge[];
}

export function getAgendaEventChain(id: string): Promise<AgendaEventChain> {
  return request<AgendaEventChain>(
    `/api/v1/agenda-events/${encodeURIComponent(id)}/chain`,
  );
}

export interface AgendaEventRevisionsResult {
  event_id: string;
  topic_id: string;
  status: string;
  confidence: string;
  human_locked_fields: string[];
  revisions: RevisionLogItem[];
}

export function listAgendaEventRevisions(
  id: string,
): Promise<AgendaEventRevisionsResult> {
  return request<AgendaEventRevisionsResult>(
    `/api/v1/agenda-events/${encodeURIComponent(id)}/revisions`,
  );
}

export function confirmAgendaEvent(
  id: string,
  note?: string,
): Promise<{ id: string; status: string }> {
  return request(`/api/v1/agenda-events/${encodeURIComponent(id)}/confirm`, {
    method: "POST",
    body: JSON.stringify({ note: note ?? "" }),
  });
}

export function dismissAgendaEvent(
  id: string,
  payload: { reason: string; false_positive: boolean },
): Promise<{ id: string; status: string }> {
  return request(`/api/v1/agenda-events/${encodeURIComponent(id)}/dismiss`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function rejectRevision(
  id: string,
  seq: number,
  reason: string,
): Promise<{ id: string }> {
  return request(
    `/api/v1/agenda-events/${encodeURIComponent(id)}/revisions/${seq}/reject`,
    {
      method: "POST",
      body: JSON.stringify({ reason }),
    },
  );
}
