/**
 * 统一 API 客户端：
 * - 解包后端统一响应 {code, data, message}，非 0 码抛 ApiError
 * - 自动携带 access_token；401 时走 /auth/refresh 单飞刷新并重试原请求一次
 * - 刷新失败：清空本地 token 并触发 onSessionExpired（由应用层跳登录页）
 */

export interface ApiEnvelope<T> {
  code: number;
  data: T;
  message: string;
}

export class ApiError extends Error {
  constructor(
    public readonly code: number,
    message: string,
    public readonly status: number,
    public readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  expires_in: number;
}

const TOKEN_STORAGE_KEY = "agendascope.tokens";

export function getStoredTokens(): TokenPair | null {
  try {
    const raw = localStorage.getItem(TOKEN_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<TokenPair>;
    if (typeof parsed.access_token !== "string" || typeof parsed.refresh_token !== "string") {
      return null;
    }
    return {
      access_token: parsed.access_token,
      refresh_token: parsed.refresh_token,
      expires_in: typeof parsed.expires_in === "number" ? parsed.expires_in : 0,
    };
  } catch {
    return null;
  }
}

export function storeTokens(tokens: TokenPair): void {
  localStorage.setItem(TOKEN_STORAGE_KEY, JSON.stringify(tokens));
}

export function clearTokens(): void {
  localStorage.removeItem(TOKEN_STORAGE_KEY);
}

type SessionExpiredHandler = () => void;
let onSessionExpired: SessionExpiredHandler = () => {};

/** 会话彻底失效（refresh 也被拒绝）时的回调，应用启动时注册（跳登录页）。 */
export function setSessionExpiredHandler(handler: SessionExpiredHandler): void {
  onSessionExpired = handler;
}

async function rawRequest<T>(
  path: string,
  init: RequestInit = {},
  accessToken?: string,
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body != null && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (accessToken) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }

  let res: Response;
  try {
    res = await fetch(path, { ...init, headers });
  } catch {
    throw new ApiError(-1, "网络异常，请稍后重试", 0);
  }

  let envelope: ApiEnvelope<T> | null = null;
  try {
    envelope = (await res.json()) as ApiEnvelope<T>;
  } catch {
    envelope = null;
  }
  if (!envelope || typeof envelope.code !== "number") {
    throw new ApiError(-1, `服务响应异常（HTTP ${res.status}）`, res.status);
  }
  if (envelope.code !== 0) {
    throw new ApiError(envelope.code, envelope.message || "请求失败", res.status, envelope.data);
  }
  return envelope.data;
}

/** 单飞刷新：并发的多个 401 共享同一次 refresh 请求（后端 refresh token 一次性轮换）。 */
let refreshInFlight: Promise<TokenPair | null> | null = null;

function refreshTokens(): Promise<TokenPair | null> {
  if (!refreshInFlight) {
    const doRefresh = async (): Promise<TokenPair | null> => {
      const tokens = getStoredTokens();
      if (!tokens) return null;
      try {
        const data = await rawRequest<TokenPair>("/api/v1/auth/refresh", {
          method: "POST",
          body: JSON.stringify({ refresh_token: tokens.refresh_token }),
        });
        const next: TokenPair = {
          access_token: data.access_token,
          refresh_token: data.refresh_token,
          expires_in: data.expires_in,
        };
        storeTokens(next);
        return next;
      } catch {
        clearTokens();
        return null;
      }
    };
    refreshInFlight = doRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

export interface RequestOptions {
  /** 默认 true：携带 access_token 并在 401 时刷新重试。登录/刷新自身传 false。 */
  auth?: boolean;
}

export async function request<T>(
  path: string,
  init: RequestInit = {},
  options: RequestOptions = {},
): Promise<T> {
  const withAuth = options.auth !== false;
  const tokens = withAuth ? getStoredTokens() : null;
  try {
    return await rawRequest<T>(path, init, tokens?.access_token);
  } catch (err) {
    // 仅在“带了 token 却被判 401”时尝试刷新；登录失败等无 token 场景直接抛出
    if (err instanceof ApiError && err.status === 401 && withAuth && tokens) {
      const refreshed = await refreshTokens();
      if (!refreshed) {
        onSessionExpired();
        throw err;
      }
      return rawRequest<T>(path, init, refreshed.access_token);
    }
    throw err;
  }
}
