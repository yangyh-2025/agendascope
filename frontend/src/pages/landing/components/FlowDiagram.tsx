import { PIPELINE_STEPS } from "../mock/stats";

/** 技术架构管线流程图(SVG + 纯 CSS 动画,无三方依赖)。 */
export default function FlowDiagram() {
  return (
    <div className="lp-flow" role="list" aria-label="技术架构">
      {PIPELINE_STEPS.map((step, i) => (
        <div key={step.id} className="lp-flow-step" role="listitem">
          <div className="lp-flow-node">
            <div className="lp-flow-node-index">{String(i + 1).padStart(2, "0")}</div>
            <div className="lp-flow-node-title">{step.title}</div>
            <div className="lp-flow-node-desc">{step.description}</div>
            <div className="lp-flow-node-tech">
              {step.tech.map((t) => (
                <span key={t} className="lp-flow-tag">
                  {t}
                </span>
              ))}
            </div>
          </div>
          {i < PIPELINE_STEPS.length - 1 && (
            <div className="lp-flow-arrow" aria-hidden="true">
              <svg width="40" height="24" viewBox="0 0 40 24" fill="none">
                <path
                  d="M0 12 L36 12 M30 6 L36 12 L30 18"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  fill="none"
                />
              </svg>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
