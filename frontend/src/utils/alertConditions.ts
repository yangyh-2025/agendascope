/** 预警规则条件描述（T4.14/T4.18）：增幅 / Top N / 负占比三条件 AND 叠加。 */
import type { AlertRule, ConditionExtra, ConditionType } from "../api/alertRules";

export const CONDITION_TYPE_LABEL: Record<ConditionType, string> = {
  growth_rate: "报道量增幅",
  top_n: "显著性 Top N",
  neg_ratio: "负面占比",
};

export function describeCondition(type: ConditionType, value: number): string {
  switch (type) {
    case "growth_rate":
      return `报道量增幅 ≥ ${value}%`;
    case "top_n":
      return `显著性进入 Top ${value}`;
    case "neg_ratio":
      return `负面占比 ≥ ${value}%`;
  }
}

/** 从规则中提取附加 AND 条件（后端存 {"and": [...]}，兼容数组形态）。 */
export function extraConditionsOf(rule: AlertRule): ConditionExtra[] {
  const extra = rule.condition_extra;
  if (!extra) return [];
  if (Array.isArray(extra)) return extra;
  return extra.and ?? [];
}

/** 规则条件的一句话描述：主条件 AND 附加条件。 */
export function describeRuleConditions(rule: AlertRule): string {
  const parts = [describeCondition(rule.condition_type, rule.condition_value)];
  for (const c of extraConditionsOf(rule)) {
    parts.push(describeCondition(c.type, c.value));
  }
  return parts.join(" 且 ");
}
