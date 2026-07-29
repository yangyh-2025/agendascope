/**
 * 许可与诊断面板：
 * - 许可状态（community/active/expired）+ 到期 30/7/1 天三级提醒（警示色）+ 到期只读提示
 * - 授权码录入（POST /system/license，同码幂等）
 * - 一键诊断包 zip 下载（POST /system/diagnostics）
 */
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../../api/client";
import {
  downloadDiagnostics,
  enrollLicense,
  fetchLicense,
  type LicenseInfo,
  type LicenseReminder,
} from "../../api/systemAdmin";

const STATUS_LABEL: Record<LicenseInfo["status"], string> = {
  community: "社区版",
  active: "已激活",
  expired: "已到期",
};

function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** 三级提醒文案：30d/7d/1d 临近到期，expired 已到期只读。 */
function reminderText(level: LicenseReminder, days: number | null): string | null {
  switch (level) {
    case "30d":
      return `许可将于 ${days ?? 30} 天后到期，请提前联系供应商续期`;
    case "7d":
      return `许可将于 ${days ?? 7} 天后到期，请尽快续期`;
    case "1d":
      return `许可将于 ${days ?? 1} 天后到期，即将进入只读状态`;
    case "expired":
      return "许可已到期：系统进入只读状态，录入有效授权码后恢复";
    default:
      return null;
  }
}

/** 提醒级别对应样式：30d 警示黄，7d/1d/expired 预警红。 */
function reminderClass(level: LicenseReminder): string {
  if (level === "30d") return "license-banner warn";
  if (level === "none") return "";
  return "license-banner danger";
}

export default function LicensePanel() {
  const [license, setLicense] = useState<LicenseInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [enrolling, setEnrolling] = useState(false);
  const [downloading, setDownloading] = useState(false);

  const load = useCallback(() => {
    setError(null);
    fetchLicense()
      .then(setLicense)
      .catch((err) => setError(err instanceof ApiError ? err.message : "许可状态加载失败"));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const enroll = () => {
    if (!code.trim()) {
      setError("请粘贴授权码");
      return;
    }
    setError(null);
    setMsg(null);
    setEnrolling(true);
    enrollLicense(code.trim())
      .then((info) => {
        setLicense(info);
        setCode("");
        setMsg("授权码已录入");
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : "授权码录入失败"))
      .finally(() => setEnrolling(false));
  };

  const download = () => {
    setError(null);
    setDownloading(true);
    downloadDiagnostics()
      .catch((err) => setError(err instanceof ApiError ? err.message : "诊断包下载失败"))
      .finally(() => setDownloading(false));
  };

  const banner = license ? reminderText(license.reminder_level, license.days_remaining) : null;

  return (
    <section className="sys-panel">
      <div className="sys-panel-head">
        <h3>许可管理</h3>
        <button type="button" className="as-btn-ghost" onClick={load}>刷新</button>
      </div>
      {error && <p className="page-error" role="alert">{error}</p>}
      {msg && <p className="status-msg">{msg}</p>}
      {!error && !license && <p className="page-loading">加载中…</p>}

      {license && (
        <>
          {banner && (
            <p className={reminderClass(license.reminder_level)} role="alert">{banner}</p>
          )}
          <dl className="system-info license-info">
            <div><dt>版本状态</dt><dd>{STATUS_LABEL[license.status]}</dd></div>
            <div><dt>产品</dt><dd>{license.product || "—"}</dd></div>
            <div><dt>许可 ID</dt><dd>{license.license_id || "—"}</dd></div>
            <div><dt>到期时间</dt><dd>{formatTime(license.expires_at)}</dd></div>
            <div>
              <dt>剩余天数</dt>
              <dd>{license.days_remaining === null ? "—" : `${license.days_remaining} 天`}</dd>
            </div>
            <div><dt>激活时间</dt><dd>{formatTime(license.activated_at)}</dd></div>
            <div>
              <dt>写入权限</dt>
              <dd className={license.write_allowed ? "" : "cell-danger"}>
                {license.write_allowed ? "正常" : "已到期只读"}
              </dd>
            </div>
            {license.note && <div><dt>说明</dt><dd>{license.note}</dd></div>}
          </dl>

          <div className="license-enroll">
            <label className="sys-field">
              <span>录入授权码</span>
              <textarea
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="粘贴供应商提供的授权码"
                rows={3}
                maxLength={2000}
              />
            </label>
            <div className="sys-actions">
              <button type="button" disabled={enrolling} onClick={enroll}>
                {enrolling ? "校验中…" : "录入授权码"}
              </button>
            </div>
          </div>
        </>
      )}

      <h4 className="sys-sub-title">运维诊断</h4>
      <p className="sys-hint">
        诊断包含脱敏配置、近期日志、健康检查与数据表计数，用于向供应商反馈问题。
      </p>
      <div className="sys-actions">
        <button type="button" className="as-btn-ghost" disabled={downloading} onClick={download}>
          {downloading ? "打包中…" : "下载诊断包（zip）"}
        </button>
      </div>
    </section>
  );
}
