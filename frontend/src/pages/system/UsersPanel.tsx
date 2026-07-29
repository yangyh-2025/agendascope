/** 用户管理面板：用户列表 + 角色修改（PATCH /system/users/{id}/role）。 */
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../../api/client";
import {
  listSystemUsers,
  updateUserRole,
  type AdminUserItem,
} from "../../api/systemAdmin";

const ROLE_LABEL: Record<string, string> = {
  registered: "注册用户",
  authorized: "授权用户",
  admin: "管理员",
};
const ROLE_OPTIONS = Object.keys(ROLE_LABEL);

function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export default function UsersPanel() {
  const [users, setUsers] = useState<AdminUserItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);

  const load = useCallback(() => {
    listSystemUsers()
      .then((r) => setUsers(r.items))
      .catch((err) => setError(err instanceof ApiError ? err.message : "用户列表加载失败"));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const changeRole = (user: AdminUserItem, role: string) => {
    if (role === user.role) return;
    setError(null);
    setMsg(null);
    setSavingId(user.id);
    updateUserRole(user.id, role)
      .then(() => {
        setMsg(`已将 ${user.username} 的角色调整为 ${ROLE_LABEL[role] ?? role}`);
        load();
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : "角色修改失败"))
      .finally(() => setSavingId(null));
  };

  return (
    <section className="sys-panel">
      <div className="sys-panel-head">
        <h3>用户管理</h3>
      </div>
      {error && <p className="page-error" role="alert">{error}</p>}
      {msg && <p className="status-msg">{msg}</p>}
      <table className="sys-table">
        <thead>
          <tr>
            <th>用户名</th>
            <th>显示名</th>
            <th>状态</th>
            <th>创建时间</th>
            <th>角色</th>
          </tr>
        </thead>
        <tbody>
          {users.length === 0 && !error && (
            <tr><td colSpan={5} className="sys-empty">加载中…</td></tr>
          )}
          {users.map((u) => (
            <tr key={u.id}>
              <td>{u.username}</td>
              <td>{u.display_name || "—"}</td>
              <td>{u.status}</td>
              <td>{formatTime(u.created_at)}</td>
              <td>
                <select
                  value={u.role}
                  disabled={savingId === u.id}
                  onChange={(e) => changeRole(u, e.target.value)}
                >
                  {ROLE_OPTIONS.map((r) => (
                    <option key={r} value={r}>{ROLE_LABEL[r]}</option>
                  ))}
                  {!ROLE_OPTIONS.includes(u.role) && (
                    <option value={u.role}>{u.role}</option>
                  )}
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
