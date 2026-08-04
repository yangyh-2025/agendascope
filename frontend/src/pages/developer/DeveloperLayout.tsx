import { Link, NavLink, Outlet } from "react-router-dom";
import { useAuthStore } from "../../stores/auth";
import "./DeveloperLayout.css";

export default function DeveloperLayout() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);

  return (
    <div className="dev-root">
      <header className="dev-nav">
        <div className="dev-nav-brand">
          <img src="/logo.svg" alt="观澜" className="dev-nav-logo" />
          <div className="dev-nav-text">
            <span className="dev-nav-name">AgendaScope · 数据开放平台</span>
            <span className="dev-nav-tag">Open Data API</span>
          </div>
        </div>
        <nav className="dev-nav-links">
          <NavLink to="/developer/docs" className={({ isActive }) => (isActive ? "active" : "")}>
            文档
          </NavLink>
          <NavLink to="/developer/keys" className={({ isActive }) => (isActive ? "active" : "")}>
            我的 Key
          </NavLink>
          <a href="https://github.com/yangyh-2025/agendascope" target="_blank" rel="noopener noreferrer">
            GitHub
          </a>
          <Link to="/dashboard" className="dev-nav-back">
            回主系统 →
          </Link>
          <span className="dev-nav-user">{user?.display_name ?? user?.username}</span>
          <button type="button" onClick={() => logout()} className="dev-nav-logout">
            退出
          </button>
        </nav>
      </header>
      <main className="dev-main">
        <Outlet />
      </main>
      <footer className="dev-footer">
        <span>© 2026 AgendaScope · 国际关系学院 · 国家安全计算模拟实验室</span>
      </footer>
    </div>
  );
}
