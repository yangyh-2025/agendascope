/**
 * 安装向导 API（T5.6）：/setup 5 步流程。
 * 向导运行于系统初始化之前，所有端点不携带鉴权（auth: false）。
 * 错误码：4005=向导已关闭（HTTP 409）；4002=步骤顺序非法；1001=参数/密码策略。
 */
import { request } from "./client";

export interface SetupProgressStage {
  key: "seed_sources" | "history_backfill" | "first_clustering";
  label: string;
  done: boolean;
  count: number;
}

export interface SetupProgress {
  stages: SetupProgressStage[];
  overall_percent: number;
}

export interface SetupStatus {
  initialized: boolean;
  current_step: number;
  completed_steps: number[];
  app_name: string | null;
  countries: string[] | null;
  progress: SetupProgress;
}

export interface EnvCheckResult {
  passed: boolean;
  cpu_cores: number;
  memory_mb: number;
  disk_gb: number;
  docker_available: boolean;
  internet_reachable: boolean;
  warnings: string[];
}

export interface SetupStepPayload {
  step: number;
  /** Step 2 */
  app_name?: string;
  /** Step 3 */
  countries?: string[];
  /** Step 4 */
  admin_username?: string;
  admin_password?: string;
}

export interface SetupStepResult {
  step: number;
  message: string;
  app_name?: string;
  countries?: string[];
  sources_disabled?: number;
  sources_enabled?: number;
  initialized?: boolean;
}

export function fetchSetupStatus(): Promise<SetupStatus> {
  return request<SetupStatus>("/api/v1/setup/status", {}, { auth: false });
}

export function fetchEnvCheck(): Promise<EnvCheckResult> {
  return request<EnvCheckResult>("/api/v1/setup/env-check", {}, { auth: false });
}

export function submitSetupStep(payload: SetupStepPayload): Promise<SetupStepResult> {
  return request<SetupStepResult>(
    "/api/v1/setup",
    { method: "POST", body: JSON.stringify(payload) },
    { auth: false },
  );
}

/** 密码策略（与后端 check_password_policy 同口径）：≥10 位且含大小写字母与数字。 */
export function passwordPolicyError(password: string): string | null {
  if (password.length < 10) return "至少 10 个字符";
  if (!/[a-z]/.test(password) || !/[A-Z]/.test(password)) return "需同时包含大写与小写字母";
  if (!/\d/.test(password)) return "需包含数字";
  return null;
}
