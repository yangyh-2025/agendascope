/** 预警配置页（T4.18）：条件构建器（三条件 AND 叠加）+ 规则列表启停。 */
import { useCallback, useEffect, useState } from "react";
import {
  createAlertRule,
  deleteAlertRule,
  listAlertRules,
  updateAlertRule,
  type AlertRule,
  type ConditionExtra,
  type ConditionType,
  type NotifyChannel,
} from "../api/alertRules";
import { ApiError } from "../api/client";
import { COUNTRIES } from "../api/meta";
import { CONDITION_TYPE_LABEL, describeRuleConditions } from "../utils/alertConditions";
import "./AlertRulesPage.css";

const CONDITION_TYPES: ConditionType[] = ["growth_rate", "top_n", "neg_ratio"];
const CHANNEL_LABEL: Record<NotifyChannel, string> = { inapp: "站内", email: "邮件", webhook: "Webhook" };

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

export default function AlertRulesPage() {
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  // 新建表单状态
  const [name, setName] = useState("");
  const [countries, setCountries] = useState<string[]>(["CN"]);
  const [keywordsInput, setKeywordsInput] = useState("");
  const [condType, setCondType] = useState<ConditionType>("growth_rate");
  const [condValue, setCondValue] = useState(50);
  const [extras, setExtras] = useState<ConditionExtra[]>([]);
  const [channels, setChannels] = useState<NotifyChannel[]>(["inapp"]);
  const [webhookUrl, setWebhookUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(() => {
    listAlertRules({ page: 1, page_size: 50 })
      .then((r) => setRules(r.items))
      .catch((err) => setError(errMsg(err, "预警规则加载失败")));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const toggleCountry = (code: string) => {
    setCountries((prev) =>
      prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code].slice(0, 10),
    );
  };

  const toggleChannel = (ch: NotifyChannel) => {
    setChannels((prev) => (prev.includes(ch) ? prev.filter((c) => c !== ch) : [...prev, ch]));
  };

  const addExtra = () => {
    const remain = CONDITION_TYPES.filter((t) => t !== condType && !extras.some((e) => e.type === t));
    if (remain.length === 0 || extras.length >= 2) return;
    setExtras([...extras, { type: remain[0], value: remain[0] === "top_n" ? 10 : 30 }]);
  };

  const submit = () => {
    setMsg(null);
    setError(null);
    if (!name.trim()) {
      setError("请填写规则名称");
      return;
    }
    if (countries.length === 0) {
      setError("请至少选择一个国家");
      return;
    }
    if (channels.length === 0) {
      setError("请至少选择一个通知通道");
      return;
    }
    setSubmitting(true);
    const keywords = keywordsInput.split(/[,，]/).map((k) => k.trim()).filter(Boolean);
    createAlertRule({
      name: name.trim(),
      country_codes: countries,
      keywords: keywords.length > 0 ? keywords.slice(0, 10) : null,
      condition_type: condType,
      condition_value: condValue,
      condition_extra: extras.length > 0 ? extras : null,
      notify_channels: channels,
      webhook_url: channels.includes("webhook") && webhookUrl.trim() ? webhookUrl.trim() : null,
    })
      .then(() => {
        setMsg("规则已创建");
        setName("");
        setExtras([]);
        load();
      })
      .catch((err) => setError(errMsg(err, "规则创建失败")))
      .finally(() => setSubmitting(false));
  };

  const toggleEnabled = (rule: AlertRule) => {
    updateAlertRule(rule.id, { enabled: !rule.enabled } as never)
      .then(load)
      .catch((err) => setError(errMsg(err, "启停失败")));
  };

  const remove = (rule: AlertRule) => {
    deleteAlertRule(rule.id)
      .then(load)
      .catch((err) => setError(errMsg(err, "删除失败")));
  };

  return (
    <div className="alert-rules-page">
      <h1>预警配置</h1>

      <div className="rule-builder">
        <h2>新建规则</h2>
        <label>
          规则名称
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="如：中美议题异常增幅" maxLength={100} />
        </label>
        <div className="builder-row">
          <span className="builder-label">监测国家</span>
          <div className="country-chips">
            {COUNTRIES.slice(0, 15).map((c) => (
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
        </div>
        <label>
          关键词（逗号分隔，可空）
          <input value={keywordsInput} onChange={(e) => setKeywordsInput(e.target.value)} placeholder="如：关税, 制裁" />
        </label>

        <div className="builder-row">
          <span className="builder-label">触发条件（AND 叠加）</span>
          <div className="condition-list">
            <div className="condition-item primary">
              <select value={condType} onChange={(e) => setCondType(e.target.value as ConditionType)}>
                {CONDITION_TYPES.filter((t) => !extras.some((x) => x.type === t)).map((t) => (
                  <option key={t} value={t}>{CONDITION_TYPE_LABEL[t]}</option>
                ))}
              </select>
              <input
                type="number"
                value={condValue}
                onChange={(e) => setCondValue(Number(e.target.value))}
                min={0}
              />
            </div>
            {extras.map((ex, i) => (
              <div key={ex.type} className="condition-item">
                <span className="and-tag">且</span>
                <select
                  value={ex.type}
                  onChange={(e) => {
                    const next = [...extras];
                    next[i] = { ...ex, type: e.target.value as ConditionType };
                    setExtras(next);
                  }}
                >
                  {CONDITION_TYPES.filter((t) => t !== condType && !extras.some((x, j) => x.type === t && j !== i)).map((t) => (
                    <option key={t} value={t}>{CONDITION_TYPE_LABEL[t]}</option>
                  ))}
                </select>
                <input
                  type="number"
                  value={ex.value}
                  onChange={(e) => {
                    const next = [...extras];
                    next[i] = { ...ex, value: Number(e.target.value) };
                    setExtras(next);
                  }}
                  min={0}
                />
                <button type="button" className="as-btn-ghost" onClick={() => setExtras(extras.filter((_, j) => j !== i))}>
                  移除
                </button>
              </div>
            ))}
            {extras.length < 2 && (
              <button type="button" className="as-btn-ghost add-cond" onClick={addExtra}>
                + 叠加条件（AND）
              </button>
            )}
          </div>
        </div>

        <div className="builder-row">
          <span className="builder-label">通知通道</span>
          <div className="channel-row">
            {(Object.entries(CHANNEL_LABEL) as [NotifyChannel, string][]).map(([ch, label]) => (
              <label key={ch} className="channel-check">
                <input type="checkbox" checked={channels.includes(ch)} onChange={() => toggleChannel(ch)} />
                {label}
              </label>
            ))}
          </div>
        </div>
        {channels.includes("webhook") && (
          <label>
            Webhook URL（企业微信/钉钉/飞书）
            <input value={webhookUrl} onChange={(e) => setWebhookUrl(e.target.value)} placeholder="https://…" />
          </label>
        )}

        <button disabled={submitting} onClick={submit}>{submitting ? "提交中…" : "创建规则"}</button>
        {msg && <p className="status-msg">{msg}</p>}
        {error && <p className="page-error" role="alert">{error}</p>}
      </div>

      <h2 className="rule-list-title">规则列表</h2>
      <div className="rule-list">
        {rules.length === 0 && <p className="page-loading">暂无规则</p>}
        {rules.map((r) => (
          <div key={r.id} className={`rule-card ${r.enabled ? "" : "disabled"}`}>
            <div className="rule-main">
              <b className="rule-name">{r.name}</b>
              <span className="rule-cond">{describeRuleConditions(r)}</span>
              <span className="rule-meta">
                {r.country_codes.join(", ")} · 通知：{r.notify_channels.map((c) => CHANNEL_LABEL[c] ?? c).join("/")}
                {r.last_triggered_at && ` · 最近触发 ${r.last_triggered_at.slice(0, 16).replace("T", " ")}`}
              </span>
            </div>
            <div className="rule-ops">
              <button className="as-btn-ghost" onClick={() => toggleEnabled(r)}>
                {r.enabled ? "停用" : "启用"}
              </button>
              <button className="as-btn-danger" onClick={() => remove(r)}>删除</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
