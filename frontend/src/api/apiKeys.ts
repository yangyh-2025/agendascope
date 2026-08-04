/** API Key 管理（用户自助，需 JWT 登录）。 */
import { request } from "./client";

export interface ApiKeyItem {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  rate_limit_per_minute: number;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string | null;
}

export interface ApiKeyCreated {
  id: string;
  name: string;
  prefix: string;
  api_key: string;
  rate_limit_per_minute: number;
  expires_at: string | null;
  created_at: string | null;
}

export function listApiKeys(): Promise<{ total: number; items: ApiKeyItem[] }> {
  return request("/api/v1/api-keys");
}

export function createApiKey(body: {
  name: string;
  rate_limit_per_minute?: number;
  expires_in_days?: number;
}): Promise<ApiKeyCreated> {
  return request("/api/v1/api-keys", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateApiKey(
  id: string,
  body: { name?: string; rate_limit_per_minute?: number },
): Promise<ApiKeyItem> {
  return request(`/api/v1/api-keys/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function revokeApiKey(id: string): Promise<ApiKeyItem> {
  return request(`/api/v1/api-keys/${encodeURIComponent(id)}`, { method: "DELETE" });
}
