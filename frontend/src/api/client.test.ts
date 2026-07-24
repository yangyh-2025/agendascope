import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  clearTokens,
  getStoredTokens,
  request,
  setSessionExpiredHandler,
  storeTokens,
  type TokenPair,
} from "./client";

const OLD_TOKENS: TokenPair = {
  access_token: "old-access",
  refresh_token: "old-refresh",
  expires_in: 1800,
};

const NEW_TOKENS: TokenPair = {
  access_token: "new-access",
  refresh_token: "new-refresh",
  expires_in: 1800,
};

function envelope(code: number, data: unknown, message = "ok", status = 200) {
  return new Response(JSON.stringify({ code, data, message }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** 返回一个 fetch mock 与按 URL 登记响应的表。 */
function stubFetchByUrl(table: Record<string, () => Response>) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      const handler = Object.entries(table).find(([prefix]) => url.startsWith(prefix));
      if (!handler) throw new Error(`未 mock 的请求: ${url}`);
      return handler[1]();
    }),
  );
  return calls;
}

describe("API 客户端 token 刷新拦截器", () => {
  beforeEach(() => {
    localStorage.clear();
    setSessionExpiredHandler(() => {});
  });

  it("401 时自动刷新 token 并用新 token 重试原请求", async () => {
    storeTokens(OLD_TOKENS);
    let sourcesCalls = 0;
    const calls = stubFetchByUrl({
      "/api/v1/sources": () => {
        sourcesCalls += 1;
        return sourcesCalls === 1
          ? envelope(2001, null, "token 已过期", 401)
          : envelope(0, { total: 1, items: [] });
      },
      "/api/v1/auth/refresh": () => envelope(0, NEW_TOKENS),
    });

    const data = await request<{ total: number }>("/api/v1/sources");

    expect(data.total).toBe(1);
    // 原请求 2 次 + refresh 1 次
    expect(calls).toHaveLength(3);
    expect(calls[0].url).toBe("/api/v1/sources");
    expect(calls[1].url).toBe("/api/v1/auth/refresh");
    expect(calls[2].url).toBe("/api/v1/sources");

    const authOf = (i: number) =>
      new Headers(calls[i].init?.headers).get("Authorization");
    expect(authOf(0)).toBe(`Bearer ${OLD_TOKENS.access_token}`);
    expect(authOf(2)).toBe(`Bearer ${NEW_TOKENS.access_token}`);

    // 新 token 已持久化
    expect(getStoredTokens()?.access_token).toBe(NEW_TOKENS.access_token);
    expect(getStoredTokens()?.refresh_token).toBe(NEW_TOKENS.refresh_token);
  });

  it("刷新失败时清空 token、触发会话失效回调并抛出原 401 错误", async () => {
    storeTokens(OLD_TOKENS);
    const onExpired = vi.fn();
    setSessionExpiredHandler(onExpired);
    stubFetchByUrl({
      "/api/v1/sources": () => envelope(2001, null, "token 已过期", 401),
      "/api/v1/auth/refresh": () => envelope(2001, null, "refresh_token 已失效，请重新登录", 401),
    });

    await expect(request("/api/v1/sources")).rejects.toMatchObject({
      name: "ApiError",
      code: 2001,
    });
    expect(getStoredTokens()).toBeNull();
    expect(onExpired).toHaveBeenCalledTimes(1);
  });

  it("无 token 的 401（如登录失败）不触发刷新", async () => {
    clearTokens();
    const calls = stubFetchByUrl({
      "/api/v1/auth/login": () => envelope(2003, null, "用户名或密码错误", 401),
    });

    await expect(
      request(
        "/api/v1/auth/login",
        { method: "POST", body: JSON.stringify({ username: "a", password: "b" }) },
        { auth: false },
      ),
    ).rejects.toBeInstanceOf(ApiError);
    expect(calls).toHaveLength(1);
  });
});
