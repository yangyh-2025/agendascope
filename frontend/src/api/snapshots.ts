/**
 * 议题显著性快照与跨国对比 API（详细设计 1.4 / 1.11）。
 *
 * 契约对齐后端 snapshots 路由：
 * - GET /api/v1/snapshots/topics/{id}?countries=CN,US&days=7
 *   响应 {topic_id, topic_name, timeline: {CC: [{window_start, article_count, salience_score, salience_rank}]}}
 *   本模块将其规整为 TopicSnapshot[]；路由不可用时回退 /topics/{id}/timeline 逐国拉取。
 * - GET /api/v1/snapshots/compare?countries=..&days=7[&topic_id=..]
 *   响应 {countries, days, per_country, disclaimer}（后端无 /topics/compare，不做错误回退）。
 */
import { ApiError, request } from "./client";

export interface SnapshotPoint {
  window_start: string;
  article_count: number;
  salience_score: number;
  salience_rank?: number | null;
  sentiment?: { pos: number; neu: number; neg: number };
}

export interface TopicSnapshot {
  topic_id: string;
  country_code: string;
  granularity: string;
  points: SnapshotPoint[];
}

/** 后端 /snapshots/compare 的 per_country 元素。 */
export interface CompareCountryPanel {
  country_code: string;
  salience_curve: { window_start: string; score: number; article_count: number }[];
  total_articles: number;
  top_topic_id: string | null;
  top_topic_name: string | null;
  coverage: "normal" | "low";
}

export interface TopicCompareResult {
  countries: string[];
  days: number;
  per_country: CompareCountryPanel[];
  disclaimer: string;
}

function isSnapshotUnavailable(err: unknown): boolean {
  // 路由不存在（404/HTTP 错误）或业务码 3001 视为“快照路由未上线”
  if (!(err instanceof ApiError)) return false;
  if (err.status === 404) return true;
  return err.code === 3001;
}

interface SnapshotsTimelineResponse {
  topic_id: string;
  topic_name: string;
  timeline: Record<
    string,
    { window_start: string; article_count: number; salience_score: number; salience_rank: number | null }[]
  >;
}

/** 获取议题在指定国家集合上的显著性快照。优先 snapshots 路由，回退 topics/timeline。 */
export async function fetchTopicSnapshots(
  topicId: string,
  countries: string[],
  days: number,
): Promise<TopicSnapshot[]> {
  const qs = new URLSearchParams({
    countries: countries.join(","),
    days: String(days),
  });
  try {
    const data = await request<SnapshotsTimelineResponse>(
      `/api/v1/snapshots/topics/${encodeURIComponent(topicId)}?${qs.toString()}`,
    );
    return Object.entries(data.timeline ?? {}).map(([cc, points]) => ({
      topic_id: data.topic_id,
      country_code: cc,
      granularity: "hour",
      points: points.map((p) => ({
        window_start: p.window_start,
        article_count: p.article_count,
        salience_score: p.salience_score,
        salience_rank: p.salience_rank,
      })),
    }));
  } catch (err) {
    if (!isSnapshotUnavailable(err)) throw err;
  }
  // 回退：逐国调 topics/{id}/timeline（后端该路由天然存在）
  const fallback = await Promise.all(
    countries.map(async (c) => {
      const q = new URLSearchParams({
        country_code: c,
        days: String(days),
        granularity: "day",
      });
      const data = await request<{
        topic_id: string;
        country_code: string | null;
        points: SnapshotPoint[];
      }>(`/api/v1/topics/${encodeURIComponent(topicId)}/timeline?${q.toString()}`);
      return {
        topic_id: data.topic_id,
        country_code: data.country_code || c,
        granularity: "day",
        points: data.points,
      };
    }),
  );
  return fallback;
}

/** 获取议题/全局跨国对比结果（含“统计关联≠因果”声明）。直接对接 /snapshots/compare。 */
export async function fetchTopicCompare(
  countries: string[],
  days: number,
  topicId?: string,
): Promise<TopicCompareResult> {
  const qs = new URLSearchParams({
    countries: countries.join(","),
    days: String(days),
  });
  if (topicId) qs.set("topic_id", topicId);
  return request<TopicCompareResult>(`/api/v1/snapshots/compare?${qs.toString()}`);
}
