/**
 * 安装守卫（T5.6）：系统未完成初始化时，/setup 以外的受保护页面一律重定向到安装向导。
 * 放在 RequireAuth 内侧（先登录守卫再安装守卫），/setup 与 /login 均在守卫之外，不会形成重定向死循环。
 * 状态查询失败时放行（fail-open）：后端故障由各页面的错误提示呈现，不阻塞入口。
 */
import { useEffect, useState } from "react";
import { Navigate, Outlet } from "react-router-dom";
import { fetchSetupStatus } from "../api/setup";

export default function SetupGuard() {
  const [initialized, setInitialized] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchSetupStatus()
      .then((s) => {
        if (!cancelled) setInitialized(s.initialized);
      })
      .catch(() => {
        if (!cancelled) setInitialized(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (initialized === null) {
    return <p className="page-loading" style={{ padding: 24 }}>加载中…</p>;
  }
  if (!initialized) {
    return <Navigate to="/setup" replace />;
  }
  return <Outlet />;
}
