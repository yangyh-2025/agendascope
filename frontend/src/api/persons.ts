/** 关键人物与机构 API（详细设计 1.9）。 */
import { request } from "./client";

export type EntityType = "person" | "thinktank" | "intl_org" | "gov_body";

export interface PersonOrgListItem {
  id: string;
  entity_type: EntityType;
  name: string;
  name_zh: string | null;
  country_code: string;
  role_title: string | null;
  monitored: boolean;
  utterance_count: number;
  latest_utterance_at: string | null;
}

export interface PersonOrgListPage {
  total: number;
  page: number;
  page_size: number;
  items: PersonOrgListItem[];
}

export interface ListPersonsOrgsParams {
  entity_type?: EntityType;
  country_code?: string;
  monitored?: boolean;
  keyword?: string;
  page?: number;
  page_size?: number;
}

export function listPersonsOrgs(
  params: ListPersonsOrgsParams = {},
): Promise<PersonOrgListPage> {
  const qs = new URLSearchParams();
  if (params.entity_type) qs.set("entity_type", params.entity_type);
  if (params.country_code) qs.set("country_code", params.country_code);
  if (typeof params.monitored === "boolean") qs.set("monitored", String(params.monitored));
  if (params.keyword) qs.set("keyword", params.keyword);
  qs.set("page", String(params.page ?? 1));
  qs.set("page_size", String(params.page_size ?? 20));
  return request<PersonOrgListPage>(`/api/v1/persons-orgs?${qs.toString()}`);
}

export interface FirstUtterance {
  quote: string;
  quote_zh?: string | null;
  topic_id: string | null;
  topic_name?: string | null;
  first_seen_at: string;
  article_id: string | null;
  confidence: string;
  media_follow_count: number;
  linked_event_id?: string | null;
}

export interface PersonOrgDetail {
  id: string;
  entity_type: EntityType;
  name: string;
  name_zh: string | null;
  name_aliases: string[];
  country_code: string;
  role_title: string | null;
  monitored: boolean;
  first_utterances: FirstUtterance[];
  degraded?: boolean;
  created_at: string;
  updated_at: string;
}

export function fetchPersonOrgDetail(id: string): Promise<PersonOrgDetail> {
  return request<PersonOrgDetail>(`/api/v1/persons-orgs/${encodeURIComponent(id)}`);
}
