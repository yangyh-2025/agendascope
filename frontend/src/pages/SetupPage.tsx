/**
 * 安装向导页（T5.6）：5 步向导 + 初始化三阶段进度条（5s 轮询）。
 * - 重进 /setup 按 GET /setup/status 恢复到 current_step，预填已保存配置
 * - initialized=true 后跳转登录页；4005（向导已关闭）同样跳登录页
 * - Step 1 环境自检为前端确认步（后端无 step 1 写端点），Step 2-5 真实落库
 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { COUNTRIES } from "../api/meta";
import {
  fetchEnvCheck,
  fetchSetupStatus,
  passwordPolicyError,
  submitSetupStep,
  type EnvCheckResult,
  type SetupStatus,
} from "../api/setup";
import "./SetupPage.css";

const STEP_LABELS = ["环境自检", "基础配置", "监控范围", "管理员账号", "完成"];
const POLL_INTERVAL_MS = 5000;
const RECOMMENDED_MEMORY_MB = 8000;

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

export default function SetupPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [step, setStep] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Step 1 环境自检
  const [env, setEnv] = useState<EnvCheckResult | null>(null);
  const [envError, setEnvError] = useState<string | null>(null);
  // Step 2 基础配置
  const [appName, setAppName] = useState("");
  // Step 3 监控范围（默认全选）
  const [countries, setCountries] = useState<string[]>(COUNTRIES.map((c) => c.code));
  // Step 4 管理员账号
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");

  const goLogin = useCallback(() => {
    navigate("/login", { replace: true });
  }, [navigate]);

  // 初始加载：恢复向导进度；已初始化直接跳登录页
  useEffect(() => {
    let cancelled = false;
    fetchSetupStatus()
      .then((s) => {
        if (cancelled) return;
        if (s.initialized) {
          goLogin();
          return;
        }
        setStatus(s);
        setStep(Math.min(Math.max(s.current_step, 1), 5));
        if (s.app_name) setAppName(s.app_name);
        if (s.countries && s.countries.length > 0) setCountries(s.countries);
      })
      .catch((err) => {
        if (!cancelled) setLoadError(errMsg(err, "向导状态加载失败"));
      });
    return () => {
      cancelled = true;
    };
  }, [goLogin]);

  // 初始化进度 5s 轮询：进度条数据 + 初始化完成自动跳登录页
  const statusLoaded = status !== null;
  useEffect(() => {
    if (!statusLoaded) return;
    const timer = setInterval(() => {
      fetchSetupStatus()
        .then((s) => {
          setStatus(s);
          if (s.initialized) goLogin();
        })
        .catch(() => {
          /* 轮询失败不打断向导，错误在操作路径上提示 */
        });
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [statusLoaded, goLogin]);

  // 进入 Step 1 时拉取环境自检
  useEffect(() => {
    if (step !== 1 || env) return;
    setEnvError(null);
    fetchEnvCheck()
      .then(setEnv)
      .catch((err) => setEnvError(errMsg(err, "环境自检失败")));
  }, [step, env]);

  const refreshEnv = () => {
    setEnv(null);
    setEnvError(null);
    fetchEnvCheck()
      .then(setEnv)
      .catch((err) => setEnvError(errMsg(err, "环境自检失败")));
  };

  const submit = (payload: Parameters<typeof submitSetupStep>[0], onOk?: () => void) => {
    setError(null);
    setMsg(null);
    setSubmitting(true);
    submitSetupStep(payload)
      .then((r) => {
        setMsg(r.message);
        onOk?.();
      })
      .catch((err) => {
        // 4005：向导已关闭（并发完成/重复安装）→ 直接跳登录页
        if (err instanceof ApiError && err.code === 4005) {
          goLogin();
          return;
        }
        setError(errMsg(err, "提交失败"));
      })
      .finally(() => setSubmitting(false));
  };

  const submitStep2 = () => {
    submit({ step: 2, app_name: appName.trim() || undefined }, () => setStep(3));
  };

  const submitStep3 = () => {
    if (countries.length === 0) {
      setError("请至少勾选一个国家");
      return;
    }
    submit({ step: 3, countries }, () => setStep(4));
  };

  const passwordError = passwordPolicyError(password);
  const submitStep4 = () => {
    if (!username.trim()) {
      setError("请填写管理员用户名");
      return;
    }
    if (passwordError) {
      setError(`密码不符合策略：${passwordError}`);
      return;
    }
    if (password !== passwordConfirm) {
      setError("两次输入的密码不一致");
      return;
    }
    submit({ step: 4, admin_username: username.trim(), admin_password: password }, () => setStep(5));
  };

  const submitStep5 = () => {
    submit({ step: 5 }, () => {
      setMsg("安装完成，正在跳转登录页…");
      setTimeout(goLogin, 1500);
    });
  };

  const toggleCountry = (code: string) => {
    setCountries((prev) =>
      prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code],
    );
  };

  const progress = status?.progress;

  return (
    <div className="setup-page">
      <div className="setup-card">
        <h1 className="setup-title">AgendaScope 观澜</h1>
        <p className="setup-subtitle">安装向导 · 全球议程设置监控平台</p>

        <ol className="setup-stepper">
          {STEP_LABELS.map((label, i) => {
            const n = i + 1;
            const cls = n < step ? "done" : n === step ? "active" : "";
            return (
              <li key={label} className={`setup-step-item ${cls}`}>
                <span className="setup-step-no">{n < step ? "✓" : n}</span>
                <span className="setup-step-label">{label}</span>
              </li>
            );
          })}
        </ol>

        {loadError && <p className="page-error" role="alert">{loadError}</p>}
        {!loadError && !status && <p className="page-loading">加载中…</p>}

        {status && (
          <>
            {step === 1 && (
              <section className="setup-panel">
                <h2>第 1 步：环境自检</h2>
                {envError && <p className="page-error" role="alert">{envError}</p>}
                {!env && !envError && <p className="page-loading">自检中…</p>}
                {env && (
                  <>
                    <ul className="env-list">
                      <li className="env-item ok">CPU：{env.cpu_cores} 核</li>
                      <li className={`env-item ${env.memory_mb > 0 && env.memory_mb < RECOMMENDED_MEMORY_MB ? "warn" : "ok"}`}>
                        内存：{env.memory_mb > 0 ? `${(env.memory_mb / 1024).toFixed(1)} GB` : "未知"}
                        {env.memory_mb > 0 && env.memory_mb < RECOMMENDED_MEMORY_MB &&
                          `（低于推荐 8 GB，缺口 ${((RECOMMENDED_MEMORY_MB - env.memory_mb) / 1024).toFixed(1)} GB，可继续）`}
                      </li>
                      <li className="env-item ok">磁盘剩余：{env.disk_gb} GB</li>
                      <li className={`env-item ${env.docker_available ? "ok" : "fail"}`}>
                        Docker：{env.docker_available ? "可用" : "不可用"}
                      </li>
                      <li className={`env-item ${env.internet_reachable ? "ok" : "warn"}`}>
                        外网连通性：{env.internet_reachable ? "正常" : "不可达（仅离线模式可用）"}
                      </li>
                    </ul>
                    {env.warnings.length > 0 && (
                      <ul className="env-warnings">
                        {env.warnings.map((w) => (
                          <li key={w}>{w}</li>
                        ))}
                      </ul>
                    )}
                    {!env.passed && (
                      <p className="env-blocking">Docker 不可用将导致部署无法进行，请先安装 Docker 20+。</p>
                    )}
                  </>
                )}
                <div className="setup-actions">
                  <button type="button" className="as-btn-ghost" onClick={refreshEnv}>重新自检</button>
                  <button type="button" onClick={() => setStep(2)}>确认并继续</button>
                </div>
              </section>
            )}

            {step === 2 && (
              <section className="setup-panel">
                <h2>第 2 步：基础配置</h2>
                <label className="setup-field">
                  <span>系统名称</span>
                  <input
                    value={appName}
                    onChange={(e) => setAppName(e.target.value)}
                    placeholder="AgendaScope 观澜"
                    maxLength={100}
                  />
                </label>
                <div className="setup-actions">
                  <button type="button" className="as-btn-ghost" onClick={() => setStep(1)}>上一步</button>
                  <button type="button" disabled={submitting} onClick={submitStep2}>
                    {submitting ? "保存中…" : "保存并继续"}
                  </button>
                </div>
              </section>
            )}

            {step === 3 && (
              <section className="setup-panel">
                <h2>第 3 步：监控范围</h2>
                <p className="setup-hint">勾选需要监控的国家（已选 {countries.length} / {COUNTRIES.length}），未勾选国家的媒体源将停用。</p>
                <div className="setup-actions scope-ops">
                  <button type="button" className="as-btn-ghost" onClick={() => setCountries(COUNTRIES.map((c) => c.code))}>
                    全选 {COUNTRIES.length} 国
                  </button>
                  <button type="button" className="as-btn-ghost" onClick={() => setCountries([])}>清空</button>
                </div>
                <div className="country-chips">
                  {COUNTRIES.map((c) => (
                    <button
                      key={c.code}
                      type="button"
                      className={`country-chip ${countries.includes(c.code) ? "active" : ""}`}
                      onClick={() => toggleCountry(c.code)}
                    >
                      {c.label}
                    </button>
                  ))}
                </div>
                <div className="setup-actions">
                  <button type="button" className="as-btn-ghost" onClick={() => setStep(2)}>上一步</button>
                  <button type="button" disabled={submitting} onClick={submitStep3}>
                    {submitting ? "保存中…" : "保存并继续"}
                  </button>
                </div>
              </section>
            )}

            {step === 4 && (
              <section className="setup-panel">
                <h2>第 4 步：管理员账号</h2>
                <label className="setup-field">
                  <span>管理员用户名</span>
                  <input
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    autoComplete="username"
                    maxLength={64}
                  />
                </label>
                <label className="setup-field">
                  <span>管理员密码</span>
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    autoComplete="new-password"
                  />
                </label>
                {password && passwordError && (
                  <p className="pwd-hint" role="alert">密码不符合策略：{passwordError}</p>
                )}
                <label className="setup-field">
                  <span>确认密码</span>
                  <input
                    type="password"
                    value={passwordConfirm}
                    onChange={(e) => setPasswordConfirm(e.target.value)}
                    autoComplete="new-password"
                  />
                </label>
                {passwordConfirm && password !== passwordConfirm && (
                  <p className="pwd-hint" role="alert">两次输入的密码不一致</p>
                )}
                <p className="setup-hint">密码策略：至少 10 个字符，且包含大写字母、小写字母与数字。</p>
                <div className="setup-actions">
                  <button type="button" className="as-btn-ghost" onClick={() => setStep(3)}>上一步</button>
                  <button type="button" disabled={submitting} onClick={submitStep4}>
                    {submitting ? "保存中…" : "保存并继续"}
                  </button>
                </div>
              </section>
            )}

            {step === 5 && (
              <section className="setup-panel">
                <h2>第 5 步：完成安装</h2>
                <p className="setup-hint">
                  确认后将写入初始化标记并关闭向导。初始化的种子源导入、历史数据回补与首次聚类在后台进行，进度见下方进度条。
                </p>
                <div className="setup-actions">
                  <button type="button" className="as-btn-ghost" onClick={() => setStep(4)}>上一步</button>
                  <button type="button" disabled={submitting} onClick={submitStep5}>
                    {submitting ? "提交中…" : "完成安装"}
                  </button>
                </div>
              </section>
            )}

            {msg && <p className="status-msg">{msg}</p>}
            {error && <p className="page-error" role="alert">{error}</p>}

            {progress && (
              <div className="setup-progress">
                <div className="setup-progress-head">
                  <span>初始化进度</span>
                  <span>{progress.overall_percent}%</span>
                </div>
                <div className="setup-progress-bar">
                  <div className="setup-progress-fill" style={{ width: `${progress.overall_percent}%` }} />
                </div>
                <ul className="setup-stages">
                  {progress.stages.map((s) => (
                    <li key={s.key} className={s.done ? "done" : ""}>
                      <span className="stage-dot">{s.done ? "✓" : "…"}</span>
                      {s.label}
                      <span className="stage-count">{s.count}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
