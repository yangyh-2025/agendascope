import { useEffect } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuthStore } from "../stores/auth";
import "./Layout.css";

interface NavItem {
  to: string;
  label: string;
  icon: string;
  end?: boolean;
}

interface NavGroup {
  title: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    title: "总览",
    items: [{ to: "/", label: "全球总览", icon: "🌐", end: true }],
  },
  {
    title: "议题分析",
    items: [
      { to: "/topics", label: "议题与事件", icon: "💬" },
      { to: "/revisions", label: "修正历史", icon: "📝" },
    ],
  },
  {
    title: "监测预警",
    items: [
      { to: "/persons", label: "人物监测", icon: "👤" },
      { to: "/analytics", label: "跨国对比", icon: "📊" },
      { to: "/alerts", label: "预警配置", icon: "🔔" },
    ],
  },
  {
    title: "内容管理",
    items: [
      { to: "/reports", label: "报告", icon: "📄" },
      { to: "/sources", label: "媒体源", icon: "📡" },
    ],
  },
  {
    title: "系统",
    items: [{ to: "/system", label: "系统管理", icon: "⚙️" }],
  },
];

/** 根据当前路径推导面包屑(分组名 / 页面名)。 */
function useBreadcrumb(): { group: string; page: string } {
  const location = useLocation();
  const path = location.pathname;
  for (const group of NAV_GROUPS) {
    for (const item of group.items) {
      if (item.end ? path === item.to : path.startsWith(item.to)) {
        return { group: group.title, page: item.label };
      }
    }
  }
  return { group: "", page: "" };
}

export default function Layout() {
  const navigate = useNavigate();
  const location = useLocation();
  const user = useAuthStore((s) => s.user);
  const loadMe = useAuthStore((s) => s.loadMe);
  const logout = useAuthStore((s) => s.logout);
  const { group, page } = useBreadcrumb();

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

  const userInitial = (user?.display_name || user?.username || "?")
    .slice(0, 1)
    .toUpperCase();

  return (
    <div className="layout">
      <aside className="layout-sidebar">
        <div className="layout-brand">
          <img className="layout-brand-mark" src="/logo.png" alt="观澜 Logo" aria-hidden="true" />
          <div className="layout-brand-text">
            <span className="layout-brand-name">观澜</span>
            <span className="layout-brand-sub">AgendaScope</span>
          </div>
        </div>
        <nav className="layout-nav">
          {NAV_GROUPS.map((g) => (
            <div key={g.title} className="layout-nav-group">
              <div className="layout-nav-group-title">{g.title}</div>
              {g.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    isActive ? "layout-nav-item active" : "layout-nav-item"
                  }
                >
                  <span className="layout-nav-icon" aria-hidden="true">
                    {item.icon}
                  </span>
                  <span className="layout-nav-label">{item.label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="layout-sidebar-footer">
          <span className="layout-version">v1.2 · © 观澜</span>
        </div>
      </aside>
      <div className="layout-main">
        <header className="layout-topbar">
          <div className="layout-topbar-left">
            {group && (
              <nav className="layout-breadcrumb" aria-label="面包屑">
                <span className="layout-breadcrumb-group">{group}</span>
                <span className="layout-breadcrumb-sep">/</span>
                <span className="layout-breadcrumb-page">{page}</span>
              </nav>
            )}
            <h1 className="layout-topbar-title">{page || "AgendaScope"}</h1>
          </div>
          <div className="layout-topbar-user">
            <div className="layout-user-info">
              <div className="layout-user-avatar" aria-hidden="true">
                {userInitial}
              </div>
              <div className="layout-user-meta">
                <span className="layout-username">
                  {user ? user.display_name || user.username : "加载中…"}
                </span>
                {user && <span className="layout-user-role">{user.role}</span>}
              </div>
            </div>
            <button className="as-btn-ghost" onClick={handleLogout}>
              退出登录
            </button>
          </div>
        </header>
        <main className="layout-content" key={location.pathname}>
          <div className="as-page-enter">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
