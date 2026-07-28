/** 预警规则 API（详细设计 1.10）。路由与后端一致：连字符风格 /alert-rules。 */
import { request } from "./client";

export type ConditionType = "growth_rate" | "top_n" | "neg_ratio";
export type NotifyChannel = "inapp" | "email" | "webhook";

export interface ConditionExtra {
  type: ConditionType;
  value: number;
}

export interface AlertRule {
  id: string;
  user_id?: string;
  name: string;
  country_codes: string[];
  topic_id: string | null;
  keywords: string[] | null;
  condition_type: ConditionType;
  condition_value: number;
  /** 附加 AND 条件（后端存 {"and": [...]}，列表项可能不下发，前端按可选处理）。 */
  condition_extra?: { and?: ConditionExtra[] } | ConditionExtra[] | null;
  notify_channels: NotifyChannel[];
  webhook_url: string | null;
  enabled: boolean;
  last_triggered_at: string | null;
  created_at: string;
}

export interface AlertRuleListPage {
  total: number;
  page: number;
  page_size: number;
  items: AlertRule[];
}

export function listAlertRules(params: { page?: number; page_size?: number } = {}) {
  const qs = new URLSearchParams({
    page: String(params.page ?? 1),
    page_size: String(params.page_size ?? 20),
  });
  return request<AlertRuleListPage>(`/api/v1/alert-rules?${qs.toString()}`);
}

export interface AlertRulePayload {
  name: string;
  country_codes: string[];
  topic_id?: string | null;
  keywords?: string[] | null;
  condition_type: ConditionType;
  condition_value: number;
  /** 附加 AND 条件列表，后端包装为 {"and": [...]}。 */
  condition_extra?: ConditionExtra[] | null;
  notify_channels: NotifyChannel[];
  webhook_url?: string | null;
}

export function createAlertRule(payload: AlertRulePayload) {
  // 后端创建响应仅含 id
  return request<{ id: string }>("/api/v1/alert-rules", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateAlertRule(id: string, payload: Partial<AlertRulePayload>) {
  // 后端更新语义为 PATCH（部分字段更新）
  return request<{ id: string }>(`/api/v1/alert-rules/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteAlertRule(id: string) {
  return request<null>(`/api/v1/alert-rules/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}
