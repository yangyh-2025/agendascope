import { create } from "zustand";
import * as authApi from "../api/auth";
import {
  clearTokens,
  getStoredTokens,
  storeTokens,
} from "../api/client";
import type { CurrentUser } from "../api/auth";

interface AuthState {
  user: CurrentUser | null;
  isAuthenticated: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  loadMe: () => Promise<void>;
  /** 会话失效（refresh 被拒）时由 API 层回调：清空状态，路由守卫负责跳登录页。 */
  expireSession: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isAuthenticated: getStoredTokens() !== null,

  async login(username, password) {
    const result = await authApi.login(username, password);
    storeTokens({
      access_token: result.access_token,
      refresh_token: result.refresh_token,
      expires_in: result.expires_in,
    });
    set({ user: result.user, isAuthenticated: true });
  },

  async logout() {
    const tokens = getStoredTokens();
    try {
      if (tokens) await authApi.logout(tokens.refresh_token);
    } catch {
      // 登出请求失败（如 token 已失效）不阻塞本地清理
    }
    clearTokens();
    set({ user: null, isAuthenticated: false });
  },

  async loadMe() {
    const user = await authApi.fetchMe();
    set({ user });
  },

  expireSession() {
    set({ user: null, isAuthenticated: false });
  },
}));
