/** 预警规则 API（详细设计 1.10）。路由使用下划线风格 /alert_rules。 */
import { request } from "./client";

export type ConditionType = "growth_rate" | "top_n" | "neg_ratio";
export type ActivePeriod = "all_day" | "custom";
export type NotifyChannel = "inapp" | "email" | "webhook";

export interface ConditionExtra {
  type: ConditionType;
  value: number;
}

export interface ActiveHours {
  start: string; // "HH:MM"
  end: string;
}

export interface AlertRule {
  id: string;
  name: string;
  country_codes: string[];
  topic_id: string | null;
  keywords: string[] | null;
  condition_type: ConditionType;
  condition_value: number;
  condition_extra: ConditionExtra[] | null;
  active_period?: ActivePeriod;
  active_hours?: ActiveHours | null;
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
  return request<AlertRuleListPage>(`/api/v1/alert_rules?${qs.toString()}`);
}

export interface AlertRulePayload {
  name: string;
  country_codes: string[];
  topic_id?: string | null;
  keywords?: string[] | null;
  condition_type: ConditionType;
  condition_value: number;
  condition_extra?: ConditionExtra[] | null;
  active_period?: ActivePeriod;
  active_hours?: ActiveHours | null;
  notify_channels: NotifyChannel[];
  webhook_url?: string | null;
  enabled?: boolean;
}

export function createAlertRule(payload: AlertRulePayload) {
  return request<{ id: string; enabled: boolean }>("/api/v1/alert_rules", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateAlertRule(id: string, payload: Partial<AlertRulePayload>) {
  return request<{ id: string }>(`/api/v1/alert_rules/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function deleteAlertRule(id: string) {
  return request<null>(`/api/v1/alert_rules/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}
