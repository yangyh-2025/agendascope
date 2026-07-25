/**
 * 议题显著性快照与跨国对比 API（详细设计 1.4 / 1.11）。
 *
 * 说明：
 * - 任务口径要求优先调 /api/v1/snapshots/topics/{id} 与 /api/v1/snapshots/compare；
 *   若后端尚未提供该路由（404/3001），自动回退到 /api/v1/topics/{id}/timeline 与
 *   /api/v1/topics/compare，保证前端在过渡期内仍可工作。
 * - 三层显著性视图与跨国对比三栏视图共用同一份数据。
 */
import { ApiError, request } from "./client";

export interface SnapshotPoint {
  window_start: string;
  article_count: number;
  salience_score: number;
  sentiment: { pos: number; neu: number; neg: number };
}

export interface TopicSnapshot {
  topic_id: string;
  country_code: string;
  granularity: string;
  points: SnapshotPoint[];
}

export interface CompareStatsEvidence {
  best_lag_days?: number | null;
  xcorr_r?: number | null;
  xcorr_p?: number | null;
  granger?: Record<string, { p: number; significant: boolean }> | null;
  direction?: string | null;
  sample_size?: number | null;
}

export interface CompareSeriesItem {
  country_code: string;
  points: Array<{
    window_start: string;
    salience_score: number;
    sentiment_neg: number;
  }>;
}

export interface TopicCompareResult {
  topic_id: string;
  countries: string[];
  cross_language_note?: string;
  series: CompareSeriesItem[];
  intermedia?: CompareStatsEvidence | null;
}

function isSnapshotUnavailable(err: unknown): boolean {
  // 路由不存在（404/HTTP 错误）或业务码 3001 视为“快照路由未上线”
  if (!(err instanceof ApiError)) return false;
  if (err.status === 404) return true;
  return err.code === 3001;
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
    return await request<TopicSnapshot[]>(
      `/api/v1/snapshots/topics/${encodeURIComponent(topicId)}?${qs.toString()}`,
    );
  } catch (err) {
    if (!isSnapshotUnavailable(err)) throw err;
  }
  // 回退：逐国调 topics/{id}/timeline
  const to = new Date();
  const from = new Date(to.getTime() - days * 24 * 3600 * 1000);
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  const fallback = await Promise.all(
    countries.map(async (c) => {
      const q = new URLSearchParams({
        country_code: c,
        from: fmt(from),
        to: fmt(to),
        granularity: "day",
      });
      const data = await request<TopicSnapshot>(
        `/api/v1/topics/${encodeURIComponent(topicId)}/timeline?${q.toString()}`,
      );
      return { ...data, country_code: data.country_code || c };
    }),
  );
  return fallback;
}

/** 获取议题跨国对比结果（含统计佐证）。优先 snapshots/compare，回退 topics/compare。 */
export async function fetchTopicCompare(
  topicId: string,
  countries: string[],
  days: number,
): Promise<TopicCompareResult> {
  const qs = new URLSearchParams({
    topic_id: topicId,
    countries: countries.join(","),
    days: String(days),
  });
  try {
    return await request<TopicCompareResult>(
      `/api/v1/snapshots/compare?${qs.toString()}`,
    );
  } catch (err) {
    if (!isSnapshotUnavailable(err)) throw err;
  }
  const to = new Date();
  const from = new Date(to.getTime() - days * 24 * 3600 * 1000);
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  const q = new URLSearchParams({
    topic_id: topicId,
    countries: countries.join(","),
    from: fmt(from),
    to: fmt(to),
  });
  return request<TopicCompareResult>(`/api/v1/topics/compare?${q.toString()}`);
}
