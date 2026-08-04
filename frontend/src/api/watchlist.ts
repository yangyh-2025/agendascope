/** 监控对象 API（关系图谱 + 边证据）。 */
import { request } from "./client";

export interface WatchlistEntity {
  id: string;
  name: string;
  name_zh: string | null;
  entity_type: string;
  country_code: string;
  role_title: string | null;
  category: string | null;
  is_seed: boolean;
  priority: number;
}

export interface WatchlistNode {
  id: string;
  name: string;
  name_en: string;
  entity_type: string;
  country_code: string;
  role_title: string | null;
  category: string;
  is_seed: boolean;
  priority: number;
}

export interface WatchlistLink {
  id: string;
  source: string;
  target: string;
  relation_type: string;
  confidence: number;
  evidence_count: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
}

export interface WatchlistGraph {
  nodes: WatchlistNode[];
  links: WatchlistLink[];
  total_nodes: number;
  total_links: number;
}

export interface RelationEvidence {
  evidence_id: string;
  article_id: string;
  article_title: string;
  article_title_translated: string | null;
  article_url: string;
  source_name: string;
  source_country_code: string;
  evidence_quote: string;
  evidence_quote_zh: string | null;
  published_at: string | null;
}

export interface RelationDetail {
  relation: {
    id: string;
    subject: { id: string; name: string } | null;
    object: { id: string; name: string } | null;
    relation_type: string;
    confidence: number;
    evidence_count: number;
  };
  total: number;
  page: number;
  page_size: number;
  items: RelationEvidence[];
}

export function fetchWatchlistGraph(params?: {
  include_peripheral?: boolean;
  min_confidence?: number;
}): Promise<WatchlistGraph> {
  const qs = new URLSearchParams();
  if (params?.include_peripheral) qs.set("include_peripheral", "true");
  if (params?.min_confidence !== undefined) qs.set("min_confidence", String(params.min_confidence));
  const suffix = qs.toString();
  return request(`/api/v1/watchlist/graph${suffix ? `?${suffix}` : ""}`);
}

export function listWatchlistEntities(params?: {
  category?: string;
  include_peripheral?: boolean;
}): Promise<{ total: number; items: WatchlistEntity[] }> {
  const qs = new URLSearchParams();
  if (params?.category) qs.set("category", params.category);
  if (params?.include_peripheral) qs.set("include_peripheral", "true");
  const suffix = qs.toString();
  return request(`/api/v1/watchlist/entities${suffix ? `?${suffix}` : ""}`);
}

export function fetchRelationEvidences(
  relationId: string,
  params?: { page?: number; page_size?: number },
): Promise<RelationDetail> {
  const qs = new URLSearchParams();
  if (params?.page) qs.set("page", String(params.page));
  if (params?.page_size) qs.set("page_size", String(params.page_size));
  const suffix = qs.toString();
  return request(
    `/api/v1/watchlist/relations/${encodeURIComponent(relationId)}/evidences${suffix ? `?${suffix}` : ""}`,
  );
}
