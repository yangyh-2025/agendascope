/**
 * 系统页（T5.10/T5.13）：
 * - admin：管理后台（系统概览 / 用户管理 / 审计日志 / 日志查看 / 许可与诊断）
 * - 非 admin：无权限提示 + 当前会话用户信息（普通用户视图）
 */
import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import { fetchMe, type CurrentUser } from "../api/auth";
import AuditLogsPanel from "./system/AuditLogsPanel";
import LicensePanel from "./system/LicensePanel";
import LogsPanel from "./system/LogsPanel";
import OverviewPanel from "./system/OverviewPanel";
import UsersPanel from "./system/UsersPanel";
import "./SystemPage.css";

type AdminTab = "overview" | "users" | "audit" | "logs" | "license";

const ADMIN_TABS: { key: AdminTab; label: string }[] = [
  { key: "overview", label: "系统概览" },
  { key: "users", label: "用户管理" },
  { key: "audit", label: "审计日志" },
  { key: "logs", label: "日志查看" },
  { key: "license", label: "许可与诊断" },
];

function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export default function SystemPage() {
  const [me, setMe] = useState<CurrentUser | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<AdminTab>("overview");

  useEffect(() => {
    let cancelled = false;
    fetchMe()
      .then((user) => {
        if (!cancelled) setMe(user);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "加载用户信息失败");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const isAdmin = me?.role === "admin";

  return (
    <section className="system-page">
      <h2 className="page-title">系统</h2>

      {error && <p className="page-error" role="alert">{error}</p>}
      {!error && !me && <p className="page-loading">加载中…</p>}

      {me && !isAdmin && (
        <>
          <p className="page-desc">系统管理功能仅管理员可用。以下为当前登录会话信息。</p>
          <dl className="system-info">
            <div><dt>用户名</dt><dd>{me.username}</dd></div>
            <div><dt>显示名</dt><dd>{me.display_name || "—"}</dd></div>
            <div><dt>角色</dt><dd>{me.role}</dd></div>
            <div><dt>邮箱</dt><dd>{me.email || "—"}</dd></div>
            <div><dt>时区</dt><dd>{me.timezone || "—"}</dd></div>
            <div><dt>最近登录</dt><dd>{formatTime(me.last_login_at)}</dd></div>
          </dl>
        </>
      )}

      {me && isAdmin && (
        <>
          <nav className="sys-tabs">
            {ADMIN_TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                className={`sys-tab ${tab === t.key ? "active" : ""}`}
                onClick={() => setTab(t.key)}
              >
                {t.label}
              </button>
            ))}
          </nav>
          {tab === "overview" && <OverviewPanel />}
          {tab === "users" && <UsersPanel />}
          {tab === "audit" && <AuditLogsPanel />}
          {tab === "logs" && <LogsPanel />}
          {tab === "license" && <LicensePanel />}
        </>
      )}
    </section>
  );
}
