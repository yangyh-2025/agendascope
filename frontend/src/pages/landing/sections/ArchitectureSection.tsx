import ScrollReveal from "../components/ScrollReveal";
import FlowDiagram from "../components/FlowDiagram";

export default function ArchitectureSection() {
  return (
    <section className="lp-section" id="architecture">
      <div className="lp-container">
        <ScrollReveal>
          <div className="lp-section-eyebrow">技术架构</div>
          <h2 className="lp-section-title">
            采集 → 聚类 → LLM → 议程引擎
            <br />
            六步全链路自动化
          </h2>
          <p className="lp-section-lede">
            每一步都可独立伸缩,故障自动降级,恢复自动回填。
          </p>
        </ScrollReveal>

        <ScrollReveal delay={120}>
          <FlowDiagram />
        </ScrollReveal>

        <div className="lp-arch-features">
          <ScrollReveal delay={0}>
            <div className="lp-arch-feature">
              <div className="lp-arch-feature-num">23</div>
              <div className="lp-arch-feature-label">LLM 池并发通道</div>
              <p>SiliconFlow × 智谱 × 讯飞星辰 8 模型,per-model 限流 + 失败转移 + 熔断冷却。</p>
            </div>
          </ScrollReveal>
          <ScrollReveal delay={100}>
            <div className="lp-arch-feature">
              <div className="lp-arch-feature-num">1024</div>
              <div className="lp-arch-feature-label">bge-m3 向量维度</div>
              <p>跨语言语义对齐,中英日韩阿拉伯语同空间比对,跨语言归并准确率实测 100%。</p>
            </div>
          </ScrollReveal>
          <ScrollReveal delay={200}>
            <div className="lp-arch-feature">
              <div className="lp-arch-feature-num">5+</div>
              <div className="lp-arch-feature-label">议题生命周期状态</div>
              <p>nascent / forming / confirmed / evolving / archived,7 天无报道自动归档。</p>
            </div>
          </ScrollReveal>
          <ScrollReveal delay={300}>
            <div className="lp-arch-feature">
              <div className="lp-arch-feature-num">0</div>
              <div className="lp-arch-feature-label">数据出机构边界</div>
              <p>Docker Compose 私有化部署;LLM/嵌入走经批准的云通道,敏感数据不出门。</p>
            </div>
          </ScrollReveal>
        </div>
      </div>
    </section>
  );
}
