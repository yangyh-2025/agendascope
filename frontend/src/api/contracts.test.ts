/** API 客户端与后端路由契约对齐回归测试（T4 缺陷修复）。 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createAlertRule, listAlertRules, updateAlertRule } from "./alertRules";
import { clearTokens } from "./client";
import { listPersonsOrgs } from "./persons";
import { listTopicArticles } from "./topics";

function stubOkFetch(data: unknown = {}) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init });
      return new Response(JSON.stringify({ code: 0, data, message: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return calls;
}

describe("API 路由契约", () => {
  beforeEach(() => {
    clearTokens();
  });

  it("alert-rules 使用连字符路径（与后端 /api/v1/alert-rules 一致）", async () => {
    const calls = stubOkFetch({ total: 0, page: 1, page_size: 20, items: [] });
    await listAlertRules();
    expect(calls[0].url).toMatch(/^\/api\/v1\/alert-rules\?/);
    expect(calls[0].url).not.toContain("alert_rules");
  });

  it("alert-rules 创建为 POST、更新为 PATCH（后端语义）", async () => {
    const calls = stubOkFetch({ id: "r1" });
    await createAlertRule({
      name: "测试规则",
      country_codes: ["CN"],
      condition_type: "growth_rate",
      condition_value: 50,
      notify_channels: ["inapp"],
    });
    expect(calls[0].url).toBe("/api/v1/alert-rules");
    expect(calls[0].init?.method).toBe("POST");

    await updateAlertRule("r1", { enabled: false });
    expect(calls[1].url).toBe("/api/v1/alert-rules/r1");
    expect(calls[1].init?.method).toBe("PATCH");
  });

  it("议题文章走 /api/v1/articles?topic_id=（后端无 /topics/{id}/articles）", async () => {
    const calls = stubOkFetch({ total: 0, page: 1, page_size: 20, items: [] });
    await listTopicArticles("topic-1", { page: 2, page_size: 10 });
    const url = new URL(`http://localhost${calls[0].url}`);
    expect(url.pathname).toBe("/api/v1/articles");
    expect(url.searchParams.get("topic_id")).toBe("topic-1");
    expect(url.searchParams.get("page")).toBe("2");
  });

  it("persons-orgs 列表路径与筛选参数", async () => {
    const calls = stubOkFetch({ total: 0, page: 1, page_size: 20, items: [] });
    await listPersonsOrgs({ entity_type: "person", country_code: "US", monitored: true });
    const url = new URL(`http://localhost${calls[0].url}`);
    expect(url.pathname).toBe("/api/v1/persons-orgs");
    expect(url.searchParams.get("entity_type")).toBe("person");
    expect(url.searchParams.get("monitored")).toBe("true");
  });
});
