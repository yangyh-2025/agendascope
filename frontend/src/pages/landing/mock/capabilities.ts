/** 9 大能力矩阵文案(对外语言润色)。 */

export interface Capability {
  icon: string;
  title: string;
  tagline: string;
  description: string;
  metric: string;
  metricLabel: string;
}

export const CAPABILITIES: Capability[] = [
  {
    icon: "🗺️",
    title: "全球议程地图",
    tagline: "108 国 × 今日报道热力",
    description:
      "实时渲染 108 个主要经济体的报道热度分布,点击任意国家下钻当日 Top 议题,一眼看清全球舆论焦点。",
    metric: "108",
    metricLabel: "监控国家/地区",
  },
  {
    icon: "🔥",
    title: "热点议题排行",
    tagline: "24h 报道量显著性 TOP 10",
    description:
      "按 24 小时报道量降序聚合显著性 TOP 10,全球/按国双视图,记者/研究员快速锁定当日议程。",
    metric: "TOP 10",
    metricLabel: "显著性议题",
  },
  {
    icon: "📡",
    title: "实时采集引擎",
    tagline: "P95 ≤ 30 分钟发布到可见",
    description:
      "124 个重点源 RSS 高频轮询 + GDELT 全球兜底,5 分钟 33 篇新文章入库;慢源超时降级,吞吐稳定。",
    metric: "≤30min",
    metricLabel: "采集到可见延迟",
  },
  {
    icon: "🔗",
    title: "议程溯源",
    tagline: "回声消除 × 跨国传播链路",
    description:
      "向量相似度折叠多国跟风报道,识别首发源;按 lag_hours 排序构建跟随国序列,还原完整传播路径。",
    metric: "0.85",
    metricLabel: "跨语言向量阈值",
  },
  {
    icon: "🧠",
    title: "多语言 AI 分析",
    tagline: "8 模型 LLM 推理池",
    description:
      "SiliconFlow × 智谱 × 讯飞星辰 8 模型并发池;跨语言向量聚类 + 命名/分类/摘要/终审全链路 LLM 化。",
    metric: "8x",
    metricLabel: "命名吞吐提升",
  },
  {
    icon: "🔁",
    title: "自我纠错机制",
    tagline: "议题演化 / 次日归并 / 全程留痕",
    description:
      "议题五态生命周期;次日跨语言归并去重;首发判定随证据自动修正,人工否决优先,revision_log 全留痕。",
    metric: "100%",
    metricLabel: "判定可追溯",
  },
  {
    icon: "👤",
    title: "人物/机构监测",
    tagline: "关键实体首发信号跟踪",
    description:
      "关键人物、智库、国际组织实体库 + NER 自动登记;同名歧义置信度衰减,首发信号自动入人工队列。",
    metric: "全量",
    metricLabel: "实体覆盖",
  },
  {
    icon: "🚨",
    title: "智能预警",
    tagline: "规则评估 + 订阅推送",
    description:
      "自定义预警规则,LLM 生成中文理由摘要;邮件/订阅推送 + 退避重试;报告自动导出。",
    metric: "P1-P3",
    metricLabel: "分级告警",
  },
  {
    icon: "📦",
    title: "私有化部署",
    tagline: "Docker Compose 单机交付",
    description:
      "一键起全栈;2 核 2G 低配置实测稳定运行;LLM/嵌入走经批准的云通道,数据不出机构边界。",
    metric: "2C2G",
    metricLabel: "最低运行配置",
  },
];
