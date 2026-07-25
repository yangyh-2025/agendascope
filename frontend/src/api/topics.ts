/**
 * 议题（topics）相关 API 调用。
 * 字段命名严格对齐后端 1.7 节 topics 模块的响应结构。
 */
import { request } from "./client";

export type LifecycleState =
  | "nascent"
  | "forming"
  | "confirmed"
  | "evolving"
  | "archived";

export type TopicSort = "salience" | "article_count" | "last_seen_at";

export interface SentimentShare {
  pos: number;
  neu: number;
  neg: number;
}

export interface TopicListItem {
  id: string;
  name: string;
  name_zh: string | null;
  topic_category: string;
  salience_score: number;
  salience_rank: number;
  rank_delta: number | null;
  article_count_24h: number;
  media_count: number;
  country_scope: string[];
  sentiment: SentimentShare;
  lifecycle_state: LifecycleState;
  confidence: string;
  naming_method: string;
  cluster_method: string;
  has_agenda_event: boolean;
  agenda_event_status: string | null;
  first_seen_at: string;
  last_seen_at: string;
}

export interface TopicListPage {
  total: number;
  page: number;
  page_size: number;
  degraded: boolean;
  items: TopicListItem[];
}

export interface ListTopicsParams {
  country_code?: string;
  lifecycle_state?: LifecycleState | "";
  topic_category?: string;
  keyword?: string;
  sort?: TopicSort;
  page?: number;
  page_size?: number;
}

export function listTopics(params: ListTopicsParams = {}): Promise<TopicListPage> {
  const qs = new URLSearchParams();
  if (params.country_code) qs.set("country_code", params.country_code);
  if (params.lifecycle_state) qs.set("lifecycle_state", params.lifecycle_state);
  if (params.topic_category) qs.set("topic_category", params.topic_category);
  if (params.keyword) qs.set("keyword", params.keyword);
  if (params.sort) qs.set("sort", params.sort);
  qs.set("page", String(params.page ?? 1));
  qs.set("page_size", String(params.page_size ?? 20));
  return request<TopicListPage>(`/api/v1/topics?${qs.toString()}`);
}

export interface TopicRevisionBrief {
  field: string;
  before_value: string;
  after_value: string;
  revised_at: string;
  actor: "machine" | "human";
}

export interface TopicAgendaEventBrief {
  id: string;
  status: string;
  origin_country_code: string;
}

export interface TopicDetail {
  id: string;
  name: string;
  name_auto: string;
  name_zh: string | null;
  topic_category: string;
  summary_zh: string | null;
  keywords: string[];
  naming_method: string;
  cluster_method: string;
  lifecycle_state: LifecycleState;
  confidence: string;
  merged_into: string | null;
  merged_from: string[];
  no_merge_with: string[];
  country_scope: string[];
  stats_24h: {
    article_count: number;
    media_count: number;
    new_countries: string[];
  };
  salience_explain: {
    formula: string;
    window: string;
    raw: Record<string, number>;
  } | null;
  agenda_events: TopicAgendaEventBrief[];
  latest_revision: TopicRevisionBrief | null;
  origin_confidence?: string;
  representative_articles?: TopicArticleItem[];
  first_seen_at: string;
  last_seen_at: string;
  created_at: string;
  updated_at: string;
}

export function getTopic(id: string): Promise<TopicDetail> {
  return request<TopicDetail>(`/api/v1/topics/${encodeURIComponent(id)}`);
}

export interface TimelinePoint {
  window_start: string;
  article_count: number;
  salience_score: number;
  salience_rank: number;
  sentiment: SentimentShare;
  peak: boolean;
  top_headlines: { article_id: string; title: string }[];
}

export interface TopicTimeline {
  topic_id: string;
  country_code: string;
  granularity: string;
  points: TimelinePoint[];
}

export function getTopicTimeline(
  id: string,
  params: { country_code?: string; from?: string; to?: string; granularity?: string } = {},
): Promise<TopicTimeline> {
  const qs = new URLSearchParams();
  if (params.country_code) qs.set("country_code", params.country_code);
  if (params.from) qs.set("from", params.from);
  if (params.to) qs.set("to", params.to);
  if (params.granularity) qs.set("granularity", params.granularity);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return request<TopicTimeline>(`/api/v1/topics/${encodeURIComponent(id)}/timeline${suffix}`);
}

export interface TopicArticleItem {
  id: string;
  title: string;
  url: string;
  source_name: string;
  country_code: string;
  published_at: string;
  weight?: number;
}

export interface TopicArticlesPage {
  total: number;
  page: number;
  page_size: number;
  items: TopicArticleItem[];
}

export function listTopicArticles(
  id: string,
  params: { page?: number; page_size?: number; sort?: string } = {},
): Promise<TopicArticlesPage> {
  const qs = new URLSearchParams();
  qs.set("page", String(params.page ?? 1));
  qs.set("page_size", String(params.page_size ?? 20));
  if (params.sort) qs.set("sort", params.sort);
  return request<TopicArticlesPage>(
    `/api/v1/topics/${encodeURIComponent(id)}/articles?${qs.toString()}`,
  );
}

export function renameTopic(
  id: string,
  payload: { name?: string; topic_category?: string },
): Promise<{ id: string; name: string; name_auto: string }> {
  return request(`/api/v1/topics/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}
