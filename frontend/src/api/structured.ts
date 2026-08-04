/** v3.0 重构新增的结构化查询 API（对应 backend structured.py router）。 */
import { request } from "./client";

export interface TopicLifecycleEvent {
  id: string;
  event_type: string;
  from_value: Record<string, unknown> | null;
  to_value: Record<string, unknown> | null;
  actor: string;
  reason: string | null;
  created_at: string | null;
}

export interface TopicLifecycleResponse {
  topic_id: string;
  topic_name: string;
  current_state: {
    status: string;
    lifecycle_state: string;
    confidence: string;
  };
  total: number;
  items: TopicLifecycleEvent[];
}

export interface TopicKeyword {
  keyword: string;
  weight: number;
  rank: number;
  source: string;
}

export interface TopicCountry {
  country_code: string;
  article_count: number;
  salience_peak: number | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
}

export interface EventFollower {
  id: string;
  sequence_no: number;
  source_id: string;
  source_name: string;
  country_code: string;
  article_id: string | null;
  followed_at: string | null;
  lag_seconds: number | null;
}

export interface EventFollowChainResponse {
  event_id: string;
  origin_at: string | null;
  origin_country_code: string;
  total_followers: number;
  items: EventFollower[];
}

export interface EntityTimelinePoint {
  window_start: string | null;
  window_end: string | null;
  mention_count: number;
  article_count: number;
  unique_sources: number;
  sentiment_avg: number | null;
  sentiment_pos: number | null;
  sentiment_neg: number | null;
  first_utterance_count: number;
  relation_new_count: number;
}

export interface EntityTimelineResponse {
  entity_id: string;
  entity_name: string;
  granularity: string;
  total: number;
  items: EntityTimelinePoint[];
}

export interface EntityArticle {
  article_id: string;
  title: string;
  title_translated: string | null;
  url: string;
  published_at: string | null;
  country_code: string;
  language: string;
  sentiment: string | null;
  mention_count: number;
  is_primary_subject: boolean;
  extracted_by: string;
  confidence: number;
}

export interface EntityArticlesResponse {
  entity_id: string;
  entity_name: string;
  total: number;
  page: number;
  page_size: number;
  items: EntityArticle[];
}

export interface ProcessingStats {
  total_articles: number;
  total_tracked: number;
  stages: Record<string, {
    pending: number;
    processing: number;
    done: number;
    failed: number;
    skipped: number;
  }>;
}

export function getTopicLifecycle(topicId: string): Promise<TopicLifecycleResponse> {
  return request(`/api/v1/structured/topics/${encodeURIComponent(topicId)}/lifecycle`);
}

export function getTopicKeywordsStructured(topicId: string): Promise<{ total: number; items: TopicKeyword[] }> {
  return request(`/api/v1/structured/topics/${encodeURIComponent(topicId)}/keywords`);
}

export function getTopicCountriesStructured(topicId: string): Promise<{ total: number; items: TopicCountry[] }> {
  return request(`/api/v1/structured/topics/${encodeURIComponent(topicId)}/countries`);
}

export function getEventFollowChain(eventId: string): Promise<EventFollowChainResponse> {
  return request(`/api/v1/structured/events/${encodeURIComponent(eventId)}/follow-chain`);
}

export function getEntityTimeline(
  entityId: string,
  params?: { granularity?: "hour" | "day" | "week"; days?: number },
): Promise<EntityTimelineResponse> {
  const qs = new URLSearchParams();
  if (params?.granularity) qs.set("granularity", params.granularity);
  if (params?.days) qs.set("days", String(params.days));
  const suffix = qs.toString();
  return request(
    `/api/v1/structured/entities/${encodeURIComponent(entityId)}/timeline${suffix ? `?${suffix}` : ""}`,
  );
}

export function getEntityArticles(
  entityId: string,
  params?: { page?: number; page_size?: number; primary_only?: boolean },
): Promise<EntityArticlesResponse> {
  const qs = new URLSearchParams();
  if (params?.page) qs.set("page", String(params.page));
  if (params?.page_size) qs.set("page_size", String(params.page_size));
  if (params?.primary_only) qs.set("primary_only", "true");
  const suffix = qs.toString();
  return request(
    `/api/v1/structured/entities/${encodeURIComponent(entityId)}/articles${suffix ? `?${suffix}` : ""}`,
  );
}

export function getProcessingStats(): Promise<ProcessingStats> {
  return request(`/api/v1/structured/processing/stats`);
}
