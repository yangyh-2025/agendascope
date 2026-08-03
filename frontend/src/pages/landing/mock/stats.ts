/** Landing 页展示用统计数据(基于 v1.3.x 真实运行数据)。 */

export interface Stat {
  value: number;
  suffix: string;
  label: string;
  hint: string;
}

export const HERO_STATS: Stat[] = [
  { value: 108, suffix: "", label: "监控国家/地区", hint: "G20 + 全球南方典型国" },
  { value: 124, suffix: "", label: "主流媒体源", hint: "每国受众覆盖 ≥70%" },
  { value: 30, suffix: "min", label: "P95 采集延迟", hint: "发布到可见" },
  { value: 24, suffix: "/7", label: "全天候监控", hint: "实时采集 + 聚类 + 告警" },
];

/** 真实生产环境运行指标(v1.3.x 实测)。 */
export interface ReliabilityMetric {
  label: string;
  value: string;
  description: string;
}

export const RELIABILITY_METRICS: ReliabilityMetric[] = [
  {
    label: "议题归并准确率",
    value: "100%",
    description: "生产回放 24 案例,跨语言向量归并全 PASS",
  },
  {
    label: "误拆率",
    value: "0%",
    description: "纯向量归并策略,同事件多角度报道零误拆",
  },
  {
    label: "误并率",
    value: "3.7%",
    description: "低于 5% 阈值,LLM 二次确认 opt-in 可再降",
  },
  {
    label: "命名吞吐",
    value: "8x",
    description: "8 模型 LLM 池并发,单轮 8 议题 14 秒完成",
  },
  {
    label: "热点接口延迟",
    value: "0.5s",
    description: "24h 议题 SQL 预过滤 + Redis 三层缓存",
  },
  {
    label: "生产稳定性",
    value: "0",
    description: "低内存优化后 5 分钟 0 次 502,load 2.96→0.48",
  },
];

/** 传播链路演示(虚构示例,展示产品能力)。 */
export interface PropagationNode {
  countryCode: string;
  countryName: string;
  time: string;
  lagHours: number;
  role: "origin" | "follower";
  outlet: string;
  headline: string;
}

export const PROPAGATION_DEMO: PropagationNode[] = [
  {
    countryCode: "US",
    countryName: "美国",
    time: "08:14 UTC",
    lagHours: 0,
    role: "origin",
    outlet: "Reuters",
    headline: "白宫宣布新的印太经济框架谈判",
  },
  {
    countryCode: "JP",
    countryName: "日本",
    time: "09:32 UTC",
    lagHours: 1.3,
    role: "follower",
    outlet: "NHK",
    headline: "米国、IPEF 交渉の次回合意を発表",
  },
  {
    countryCode: "KR",
    countryName: "韩国",
    time: "10:05 UTC",
    lagHours: 1.9,
    role: "follower",
    outlet: "연합뉴스",
    headline: "美, 인도·태평양 경제프레임워크 협상 진전",
  },
  {
    countryCode: "IN",
    countryName: "印度",
    time: "11:47 UTC",
    lagHours: 3.5,
    role: "follower",
    outlet: "PTI",
    headline: "US announces progress in IPEF trade talks",
  },
  {
    countryCode: "GB",
    countryName: "英国",
    time: "13:22 UTC",
    lagHours: 5.1,
    role: "follower",
    outlet: "BBC",
    headline: "Indo-Pacific economic framework advances",
  },
  {
    countryCode: "DE",
    countryName: "德国",
    time: "15:08 UTC",
    lagHours: 6.9,
    role: "follower",
    outlet: "DW",
    headline: "USA treiben Indopazifik-Wirtschaftsrahmen voran",
  },
  {
    countryCode: "BR",
    countryName: "巴西",
    time: "18:41 UTC",
    lagHours: 10.4,
    role: "follower",
    outlet: "Globo",
    headline: "EUA avançam em acordo econômico do Indo-Pacífico",
  },
];

/** 技术架构管线步骤。 */
export interface PipelineStep {
  id: string;
  title: string;
  description: string;
  tech: string[];
}

export const PIPELINE_STEPS: PipelineStep[] = [
  {
    id: "collect",
    title: "采集",
    description: "RSS 高频轮询 + GDELT 兜底,源健康状态机自动升降级",
    tech: ["RSS", "GDELT", "Source Health"],
  },
  {
    id: "nlp",
    title: "NLP",
    description: "fastText 语言识别 → bge-m3 1024 维向量嵌入 → 流式入队",
    tech: ["fastText", "bge-m3", "SiliconFlow"],
  },
  {
    id: "cluster",
    title: "聚类",
    description: "Agglomerative 硬阈值 + BERTopic 双策略,在线增量归簇",
    tech: ["Agglomerative", "BERTopic", "HNSW"],
  },
  {
    id: "llm",
    title: "LLM 标注",
    description: "8 模型池并发,命名/分类/摘要/首发判定/终审全链路",
    tech: ["GLM", "Qwen", "DeepSeek", "讯飞星辰"],
  },
  {
    id: "agenda",
    title: "议程引擎",
    description: "回声消除 + 生命周期 + 次日归并 + 首发源判定 + 终审",
    tech: ["Echo Cancel", "Lifecycle", "Revision"],
  },
  {
    id: "present",
    title: "呈现",
    description: "全球地图 + 议题详情 + 传播链路 + 告警 + 报告导出",
    tech: ["React", "ECharts", "FastAPI"],
  },
];
