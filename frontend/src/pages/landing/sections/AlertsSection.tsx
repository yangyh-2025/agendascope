import ScrollReveal from "../components/ScrollReveal";
import { ALERT_DEMO, ALERT_NOTIFICATIONS } from "../mock/demos";

const LEVEL_COLOR: Record<string, string> = {
  P1: "#ff3b5c",
  P2: "#f59e0b",
  P3: "#4f7fff",
};

const LEVEL_BG: Record<string, string> = {
  P1: "rgba(255,59,92,0.12)",
  P2: "rgba(245,158,11,0.12)",
  P3: "rgba(79,127,255,0.12)",
};

const STATUS_LABEL: Record<string, string> = {
  triggered: "已触发",
  watching: "观察中",
  ok: "正常",
};

export default function AlertsSection() {
  return (
    <section className="lp-section lp-section-alt" id="alerts">
      <div className="lp-container">
        <ScrollReveal>
          <div className="lp-section-eyebrow">智能预警</div>
          <h2 className="lp-section-title">
            规则评估 + LLM 摘要
            <br />
            分级告警实时推送
          </h2>
          <p className="lp-section-lede">
            自定义预警规则,触发时 LLM 生成中文理由摘要。
            <br />
            邮件 / 订阅 / Webhook 多通道推送,退避重试,分级响应。
          </p>
        </ScrollReveal>

        <div className="lp-alerts-grid">
          {/* 左:规则评估状态 */}
          <ScrollReveal delay={0}>
            <div className="lp-alerts-rules">
              <div className="lp-alerts-header">
                <span className="lp-alerts-title">预警规则</span>
                <span className="lp-alerts-count">{ALERT_DEMO.length} 条启用</span>
              </div>
              <div className="lp-alerts-rule-list">
                {ALERT_DEMO.map((rule) => (
                  <div
                    key={rule.id}
                    className={`lp-alert-rule lp-alert-rule-${rule.status}`}
                    style={{
                      borderLeftColor: LEVEL_COLOR[rule.level],
                      background: rule.status === "triggered" ? LEVEL_BG[rule.level] : undefined,
                    }}
                  >
                    <div className="lp-alert-rule-header">
                      <span
                        className="lp-alert-level"
                        style={{ color: LEVEL_COLOR[rule.level], background: LEVEL_BG[rule.level] }}
                      >
                        {rule.level}
                      </span>
                      <span className="lp-alert-name">{rule.name}</span>
                      <span className={`lp-alert-status lp-alert-status-${rule.status}`}>
                        {STATUS_LABEL[rule.status]}
                      </span>
                      {rule.triggeredAt && (
                        <span className="lp-alert-time">{rule.triggeredAt}</span>
                      )}
                    </div>
                    <div className="lp-alert-condition">{rule.condition}</div>
                    {rule.status === "triggered" && rule.summary && (
                      <div className="lp-alert-summary">
                        <span className="lp-alert-summary-icon">✨</span>
                        <span className="lp-alert-summary-text">
                          <strong>LLM 摘要:</strong>
                          {rule.summary}
                        </span>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </ScrollReveal>

          {/* 右:推送通道 + 触发时间线 */}
          <ScrollReveal delay={120}>
            <div className="lp-alerts-side">
              <div className="lp-alerts-channels">
                <div className="lp-alerts-side-title">推送通道</div>
                {ALERT_NOTIFICATIONS.map((n, i) => (
                  <div key={i} className="lp-alert-channel">
                    <div className="lp-alert-channel-icon">
                      {n.channel === "邮件" ? "📧" : n.channel === "订阅" ? "📮" : "🔗"}
                    </div>
                    <div className="lp-alert-channel-body">
                      <div className="lp-alert-channel-name">{n.channel}</div>
                      <div className="lp-alert-channel-target">{n.target}</div>
                    </div>
                    <div className={`lp-alert-channel-status lp-alert-channel-${n.status}`}>
                      {n.status === "delivered" ? "✓ 已送达" : n.status === "sent" ? "↑ 已发送" : "✗ 失败"}
                    </div>
                  </div>
                ))}
              </div>

              <div className="lp-alerts-flow">
                <div className="lp-alerts-side-title">触发 → 推送 全链路</div>
                <div className="lp-alert-flow-steps">
                  <div className="lp-alert-flow-step">
                    <div className="lp-alert-flow-num">1</div>
                    <div className="lp-alert-flow-text">
                      <div className="lp-alert-flow-name">规则评估</div>
                      <div className="lp-alert-flow-desc">每 5 分钟扫描议题指标</div>
                    </div>
                  </div>
                  <div className="lp-alert-flow-arrow">↓</div>
                  <div className="lp-alert-flow-step">
                    <div className="lp-alert-flow-num">2</div>
                    <div className="lp-alert-flow-text">
                      <div className="lp-alert-flow-name">LLM 摘要</div>
                      <div className="lp-alert-flow-desc">生成中文理由与影响判断</div>
                    </div>
                  </div>
                  <div className="lp-alert-flow-arrow">↓</div>
                  <div className="lp-alert-flow-step">
                    <div className="lp-alert-flow-num">3</div>
                    <div className="lp-alert-flow-text">
                      <div className="lp-alert-flow-name">多通道推送</div>
                      <div className="lp-alert-flow-desc">邮件/订阅/Webhook,失败退避重试</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </ScrollReveal>
        </div>
      </div>
    </section>
  );
}
