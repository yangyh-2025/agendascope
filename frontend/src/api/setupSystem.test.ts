/** 安装向导与系统管理后台 API 契约测试（T5.6/T5.10/T5.13）。 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { clearTokens, storeTokens } from "./client";
import {
  fetchEnvCheck,
  fetchSetupStatus,
  passwordPolicyError,
  submitSetupStep,
} from "./setup";
import {
  enrollLicense,
  fetchLicense,
  fetchSystemLogs,
  fetchSystemOverview,
  listAuditLogs,
  listSystemUsers,
  updateUserRole,
} from "./systemAdmin";

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

describe("安装向导 API 契约", () => {
  beforeEach(() => {
    clearTokens();
  });

  it("status/env-check 不携带鉴权（向导运行于初始化之前）", async () => {
    storeTokens({ access_token: "a", refresh_token: "r", expires_in: 1800 });
    const calls = stubOkFetch({});
    await fetchSetupStatus();
    await fetchEnvCheck();
    expect(calls[0].url).toBe("/api/v1/setup/status");
    expect(calls[1].url).toBe("/api/v1/setup/env-check");
    for (const call of calls) {
      const headers = new Headers(call.init?.headers);
      expect(headers.has("Authorization")).toBe(false);
    }
  });

  it("步骤提交为 POST /api/v1/setup，体为 {step, ...}", async () => {
    const calls = stubOkFetch({ step: 3, message: "ok" });
    await submitSetupStep({ step: 3, countries: ["CN", "US"] });
    expect(calls[0].url).toBe("/api/v1/setup");
    expect(calls[0].init?.method).toBe("POST");
    const body = JSON.parse(String(calls[0].init?.body));
    expect(body).toEqual({ step: 3, countries: ["CN", "US"] });
  });

  it("密码策略与后端同口径：≥10 位且含大小写字母与数字", () => {
    expect(passwordPolicyError("Short1A")).toBeTruthy();
    expect(passwordPolicyError("alllowercase1")).toBeTruthy();
    expect(passwordPolicyError("ALLUPPERCASE1")).toBeTruthy();
    expect(passwordPolicyError("NoDigitsHere")).toBeTruthy();
    expect(passwordPolicyError("ValidPass123")).toBeNull();
  });
});

describe("系统管理后台 API 契约", () => {
  beforeEach(() => {
    clearTokens();
  });

  it("概览与用户列表路径", async () => {
    const calls = stubOkFetch({ items: [] });
    await fetchSystemOverview();
    await listSystemUsers();
    expect(calls[0].url).toBe("/api/v1/system/overview");
    expect(calls[1].url).toBe("/api/v1/system/users");
  });

  it("角色修改为 PATCH /system/users/{id}/role", async () => {
    const calls = stubOkFetch({ id: "u1", role: "authorized" });
    await updateUserRole("u1", "authorized");
    expect(calls[0].url).toBe("/api/v1/system/users/u1/role");
    expect(calls[0].init?.method).toBe("PATCH");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ role: "authorized" });
  });

  it("审计日志过滤参数与分页", async () => {
    const calls = stubOkFetch({ items: [], total: 0, page: 2, page_size: 20 });
    await listAuditLogs(
      { start: "2026-07-01T00:00:00Z", actor: "admin", action: "auth.login", result: "denied" },
      2,
      20,
    );
    const url = new URL(`http://localhost${calls[0].url}`);
    expect(url.pathname).toBe("/api/v1/system/audit-logs");
    expect(url.searchParams.get("start")).toBe("2026-07-01T00:00:00Z");
    expect(url.searchParams.get("actor")).toBe("admin");
    expect(url.searchParams.get("action")).toBe("auth.login");
    expect(url.searchParams.get("result")).toBe("denied");
    expect(url.searchParams.get("page")).toBe("2");
    expect(url.searchParams.get("page_size")).toBe("20");
  });

  it("日志查看器 level/lines 查询参数", async () => {
    const calls = stubOkFetch({ items: [], matched: 0, truncated: false });
    await fetchSystemLogs("ERROR", 500);
    const url = new URL(`http://localhost${calls[0].url}`);
    expect(url.pathname).toBe("/api/v1/system/logs");
    expect(url.searchParams.get("level")).toBe("ERROR");
    expect(url.searchParams.get("lines")).toBe("500");
  });

  it("许可查询 GET、录入 POST {code}", async () => {
    const calls = stubOkFetch({ status: "community" });
    await fetchLicense();
    await enrollLicense("LICENSE-CODE");
    expect(calls[0].url).toBe("/api/v1/system/license");
    expect(calls[0].init?.method ?? "GET").toBe("GET");
    expect(calls[1].url).toBe("/api/v1/system/license");
    expect(calls[1].init?.method).toBe("POST");
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ code: "LICENSE-CODE" });
  });
});
