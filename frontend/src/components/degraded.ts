/** 由后端方法字段推导降级标注类型（T4.13 标注体系与后端取值对齐）。 */
import type { DegradedKind } from "./DegradedBadge";

/** naming_method=ctfidf_fallback/keyword_fallback 表示 LLM 降级期产出。 */
const LLM_FALLBACK_METHODS = new Set(["ctfidf_fallback", "keyword_fallback"]);
/** cluster_method=keyword_fallback 表示 BERTopic 降级期粗聚类。 */
const CLUSTER_FALLBACK_METHODS = new Set(["keyword_fallback"]);

export function degradedKindsOf(fields: {
  naming_method?: string | null;
  cluster_method?: string | null;
}): DegradedKind[] {
  const kinds: DegradedKind[] = [];
  if (fields.naming_method && LLM_FALLBACK_METHODS.has(fields.naming_method)) {
    kinds.push("llm_degraded");
  }
  if (fields.cluster_method && CLUSTER_FALLBACK_METHODS.has(fields.cluster_method)) {
    kinds.push("cluster_degraded");
  }
  return kinds;
}
