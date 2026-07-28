import { describe, expect, it } from "vitest";
import type { AlertRule } from "../api/alertRules";
import { describeCondition, describeRuleConditions, extraConditionsOf } from "./alertConditions";

const baseRule: AlertRule = {
  id: "r1",
  name: "测试",
  country_codes: ["CN"],
  topic_id: null,
  keywords: null,
  condition_type: "growth_rate",
  condition_value: 50,
  notify_channels: ["inapp"],
  webhook_url: null,
  enabled: true,
  last_triggered_at: null,
  created_at: "2026-07-01T00:00:00",
};

describe("预警规则条件描述", () => {
  it("三类条件的文案", () => {
    expect(describeCondition("growth_rate", 50)).toBe("报道量增幅 ≥ 50%");
    expect(describeCondition("top_n", 10)).toBe("显著性进入 Top 10");
    expect(describeCondition("neg_ratio", 30)).toBe("负面占比 ≥ 30%");
  });

  it("附加 AND 条件兼容 {\"and\": [...]} 与数组两种形态", () => {
    expect(extraConditionsOf({ ...baseRule, condition_extra: { and: [{ type: "top_n", value: 5 }] } }))
      .toEqual([{ type: "top_n", value: 5 }]);
    expect(extraConditionsOf({ ...baseRule, condition_extra: [{ type: "neg_ratio", value: 20 }] }))
      .toEqual([{ type: "neg_ratio", value: 20 }]);
    expect(extraConditionsOf(baseRule)).toEqual([]);
  });

  it("多条件 AND 叠加描述", () => {
    const rule: AlertRule = {
      ...baseRule,
      condition_extra: { and: [{ type: "top_n", value: 10 }, { type: "neg_ratio", value: 30 }] },
    };
    expect(describeRuleConditions(rule)).toBe("报道量增幅 ≥ 50% 且 显著性进入 Top 10 且 负面占比 ≥ 30%");
  });
});
