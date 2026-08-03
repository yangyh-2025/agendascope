import { useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { changePassword } from "../api/auth";
import { passwordPolicyError } from "../api/setup";
import { useAuthStore } from "../stores/auth";
import "./LoginPage.css";

export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const login = useAuthStore((s) => s.login);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // 首次登录强制改密（后端 must_change_password 闭环：改密前业务接口一律 403/2005）
  const [mustChange, setMustChange] = useState(false);
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname ?? "/dashboard";

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      await login(username.trim(), password);
      if (useAuthStore.getState().user?.must_change_password) {
        setMustChange(true);
        return;
      }
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "登录失败,请稍后重试");
    } finally {
      setSubmitting(false);
    }
  };

  const handleChangePassword = async (e: FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setError(null);
    const policyError = passwordPolicyError(newPassword);
    if (policyError) {
      setError(policyError);
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("两次输入的新密码不一致");
      return;
    }
    setSubmitting(true);
    try {
      // 改密成功会吊销全部会话，需用新密码重新登录
      await changePassword(password, newPassword);
      await login(username.trim(), newPassword);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "修改密码失败,请稍后重试");
    } finally {
      setSubmitting(false);
    }
  };

  if (mustChange) {
    return (
      <div className="login-page">
        <form className="login-card" onSubmit={handleChangePassword}>
          <div className="login-brand">
            <img className="login-brand-mark" src="/logo.png" alt="观澜 Logo" aria-hidden="true" />
            <h1 className="login-title">首次登录请修改密码</h1>
            <p className="login-subtitle">初始密码仅限首次使用,请设置新密码(≥10 位,含大小写字母与数字)</p>
          </div>

          <label className="login-field">
            <span>新密码</span>
            <input
              name="new-password"
              type="password"
              autoComplete="new-password"
              placeholder="请输入新密码"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
            />
          </label>
          <label className="login-field">
            <span>确认新密码</span>
            <input
              name="confirm-password"
              type="password"
              autoComplete="new-password"
              placeholder="请再次输入新密码"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
            />
          </label>

          {error && (
            <p className="login-error" role="alert">
              {error}
            </p>
          )}

          <button type="submit" disabled={submitting}>
            {submitting ? "提交中…" : "修改并登录"}
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={handleSubmit}>
        <div className="login-brand">
          <img className="login-brand-mark" src="/logo.png" alt="观澜 Logo" aria-hidden="true" />
          <h1 className="login-title">观澜</h1>
          <p className="login-subtitle">AgendaScope · 全球议程设置监控平台</p>
        </div>

        <label className="login-field">
          <span>账号</span>
          <input
            name="username"
            autoComplete="username"
            placeholder="请输入账号"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </label>
        <label className="login-field">
          <span>密码</span>
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            placeholder="请输入密码"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>

        {error && (
          <p className="login-error" role="alert">
            {error}
          </p>
        )}

        <button type="submit" disabled={submitting}>
          {submitting ? "登录中…" : "登 录"}
        </button>
      </form>
    </div>
  );
}
