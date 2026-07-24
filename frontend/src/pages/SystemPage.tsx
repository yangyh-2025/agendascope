import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import { fetchMe, type CurrentUser } from "../api/auth";
import "./SystemPage.css";

function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** 系统页：展示当前会话用户信息（来自 GET /auth/me 实时查询）。 */
export default function SystemPage() {
  const [me, setMe] = useState<CurrentUser | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <section>
      <h2 className="page-title">系统</h2>
      <p className="page-desc">当前登录会话信息（实时查询自认证服务）。</p>

      {error && <p className="page-error" role="alert">{error}</p>}
      {!error && !me && <p className="page-loading">加载中…</p>}

      {me && (
        <dl className="system-info">
          <div><dt>用户名</dt><dd>{me.username}</dd></div>
          <div><dt>显示名</dt><dd>{me.display_name || "—"}</dd></div>
          <div><dt>角色</dt><dd>{me.role}</dd></div>
          <div><dt>邮箱</dt><dd>{me.email || "—"}</dd></div>
          <div><dt>时区</dt><dd>{me.timezone || "—"}</dd></div>
          <div><dt>最近登录</dt><dd>{formatTime(me.last_login_at)}</dd></div>
        </dl>
      )}
    </section>
  );
}
