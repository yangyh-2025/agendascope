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
  /** 后端列表当前下发 article_count（总文章数）；24h 口径为可选增强。 */
  article_count?: number;
  media_count?: number;
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

/** 议题 revision_log 条目（后端详情直接内嵌 revision_log 数组）。 */
export interface TopicRevisionEntry {
  seq: number;
  revised_at: string;
  field: string;
  before_value: unknown;
  after_value: unknown;
  trigger_evidence?: Record<string, unknown> | null;
  actor: "machine" | "human";
  actor_id?: string | null;
  model?: string | null;
  prompt_version?: string | null;
  trigger?: string;
}

export interface TopicAgendaEventBrief {
  id: string;
  status: string;
  origin_country_code: string;
  confidence?: string;
  origin_at?: string | null;
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
  /** 已 merged_into 的源议题由后端下发 redirect_topic_id（301 语义）。 */
  redirect_topic_id?: string;
  revision_log: TopicRevisionEntry[];
  human_locked_fields?: string[];
  llm_model?: string | null;
  prompt_version?: string | null;
  stats_24h?: {
    article_count: number;
    media_count: number;
    new_countries: string[];
  } | null;
  salience_explain?: {
    formula: string;
    window: string;
    raw: Record<string, number>;
  } | null;
  agenda_events: TopicAgendaEventBrief[];
  latest_revision?: TopicRevisionBrief | null;
  origin_confidence?: string;
  representative_articles?: TopicArticleItem[];
  first_seen_at: string;
  last_seen_at: string;
  created_at: string;
  updated_at: string;
}

export interface HotTopicItem {
  id: string;
  name: string;
  name_zh: string | null;
  topic_category: string;
  salience_score: number;
  salience_country: string | null;
  article_count: number;
  media_count: number;
  has_agenda_event: boolean;
  country_scope: string[];
}

/** 全局热点议题 TOP N（GET /topics/hot，总览页右侧卡片）。 */
export function listHotTopics(limit = 10): Promise<{ items: HotTopicItem[]; total: number }> {
  return request(`/api/v1/topics/hot?limit=${limit}`);
}

export function getTopic(id: string): Promise<TopicDetail> {
  return request<TopicDetail>(`/api/v1/topics/${encodeURIComponent(id)}`);
}

/** 归并建议候选（GET /topics/{id}/merge-suggestions，authorized+）。 */
export interface MergeSuggestion {
  topic_id: string;
  name: string;
  name_zh: string | null;
  lifecycle_state: string;
  similarity: number;
  country_scope: string[];
  in_no_merge_list: boolean;
}

export function getMergeSuggestions(
  id: string,
): Promise<{ topic_id: string; suggestions: MergeSuggestion[]; reason?: string }> {
  return request(`/api/v1/topics/${encodeURIComponent(id)}/merge-suggestions`);
}

/** 议题分裂/误并回滚（POST /topics/{parent_id}/split）。 */
export function splitTopic(
  parentId: string,
  childTopicId: string,
): Promise<{ parent_id: string; child_id: string; restored_topic_id: string }> {
  return request(`/api/v1/topics/${encodeURIComponent(parentId)}/split`, {
    method: "POST",
    body: JSON.stringify({ child_topic_id: childTopicId }),
  });
}

export interface TimelinePoint {
  window_start: string;
  article_count: number;
  salience_score: number;
  salience_rank: number | null;
  /** 后端按国家聚合时下发；全局时间线为 null。 */
  country_code?: string | null;
  /** 后端当前下发 top_attributes 关键词；情感/峰值/头条为可选增强字段。 */
  top_attributes?: string[];
  sentiment?: SentimentShare;
  peak?: boolean;
  top_headlines?: { article_id: string; title: string }[];
}

export interface TopicTimeline {
  topic_id: string;
  country_code: string | null;
  granularity: string;
  days?: number;
  points: TimelinePoint[];
}

export function getTopicTimeline(
  id: string,
  params: { country_code?: string; days?: number; granularity?: string } = {},
): Promise<TopicTimeline> {
  const qs = new URLSearchParams();
  if (params.country_code) qs.set("country_code", params.country_code);
  if (params.days) qs.set("days", String(params.days));
  if (params.granularity) qs.set("granularity", params.granularity);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return request<TopicTimeline>(`/api/v1/topics/${encodeURIComponent(id)}/timeline${suffix}`);
}

export interface TopicArticleItem {
  id: string;
  title: string;
  url: string;
  source_name: string | null;
  country_code: string;
  published_at: string | null;
  /** L1 版权合规：仅 ≤150 字摘录，不出全文。 */
  excerpt?: string;
  weight?: number;
}

export interface TopicArticlesPage {
  total: number;
  page: number;
  page_size: number;
  degraded?: boolean;
  degrade_reason?: string | null;
  items: TopicArticleItem[];
}

/**
 * 议题相关文章：后端无 /topics/{id}/articles 端点，
 * 统一走 /api/v1/articles?topic_id= 检索（L1：标题+摘录+原文链接）。
 */
export function listTopicArticles(
  id: string,
  params: { page?: number; page_size?: number } = {},
): Promise<TopicArticlesPage> {
  const qs = new URLSearchParams();
  qs.set("topic_id", id);
  qs.set("page", String(params.page ?? 1));
  qs.set("page_size", String(params.page_size ?? 20));
  return request<TopicArticlesPage>(`/api/v1/articles?${qs.toString()}`);
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
