import { request, type TokenPair } from "./client";

export interface CurrentUser {
  id: string;
  username: string;
  display_name: string;
  role: string;
  email?: string | null;
  locale?: string | null;
  timezone?: string | null;
  last_login_at?: string | null;
  must_change_password?: boolean;
}

export interface LoginResult extends TokenPair {
  user: CurrentUser;
}

export function login(username: string, password: string): Promise<LoginResult> {
  return request<LoginResult>(
    "/api/v1/auth/login",
    { method: "POST", body: JSON.stringify({ username, password }) },
    { auth: false },
  );
}

export function logout(refreshToken: string): Promise<null> {
  return request<null>("/api/v1/auth/logout", {
    method: "POST",
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
}

export function fetchMe(): Promise<CurrentUser> {
  return request<CurrentUser>("/api/v1/auth/me");
}

export function changePassword(oldPassword: string, newPassword: string): Promise<null> {
  return request<null>("/api/v1/auth/change-password", {
    method: "POST",
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });
}
