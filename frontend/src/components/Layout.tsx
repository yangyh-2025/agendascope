import { useEffect } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuthStore } from "../stores/auth";
import "./Layout.css";

const NAV_ITEMS = [
  { to: "/", label: "看板", end: true },
  { to: "/sources", label: "媒体源", end: false },
  { to: "/system", label: "系统", end: false },
];

export default function Layout() {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const loadMe = useAuthStore((s) => s.loadMe);
  const logout = useAuthStore((s) => s.logout);

  useEffect(() => {
    if (!user) {
      loadMe().catch(() => {
        /* 401 由 API 层刷新/会话失效流程处理 */
      });
    }
  }, [user, loadMe]);

  const handleLogout = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  return (
    <div className="layout">
      <aside className="layout-sidebar">
        <div className="layout-brand">
          <span className="layout-brand-name">AgendaScope 观澜</span>
          <span className="layout-brand-sub">全球议程设置监控平台</span>
        </div>
        <nav className="layout-nav">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                isActive ? "layout-nav-item active" : "layout-nav-item"
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="layout-main">
        <header className="layout-topbar">
          <span className="layout-topbar-title">Phase 1 · 全球媒体源监控</span>
          <div className="layout-topbar-user">
            <span className="layout-username">
              {user ? `${user.display_name || user.username}（${user.role}）` : "加载中…"}
            </span>
            <button className="as-btn-ghost" onClick={handleLogout}>
              退出登录
            </button>
          </div>
        </header>
        <main className="layout-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
