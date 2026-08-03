/**
 * 4 个演示板块的 mock 数据(虚构,仅展示产品能力)。
 * 与 stats.ts 的 PROPAGATION_DEMO 风格一致。
 */

/** 实时采集演示:各国媒体的滚动 feed。 */
export interface CollectionItem {
  countryCode: string;
  countryName: string;
  outlet: string;
  headline: string;
  time: string;  // "刚刚" / "2分钟前" 等
  language: string;
}

export const COLLECTION_FEED: CollectionItem[] = [
  { countryCode: "US", countryName: "美国", outlet: "Reuters", headline: "White House announces new Indo-Pacific trade framework", time: "刚刚", language: "EN" },
  { countryCode: "CN", countryName: "中国", outlet: "新华社", headline: "国务院常务会议部署下半年经济工作", time: "12秒前", language: "ZH" },
  { countryCode: "JP", countryName: "日本", outlet: "NHK", headline: "首相、経済対策の新方針を表明", time: "48秒前", language: "JA" },
  { countryCode: "GB", countryName: "英国", outlet: "BBC", headline: "Chancellor outlines new fiscal measures", time: "1分钟前", language: "EN" },
  { countryCode: "RU", countryName: "俄罗斯", outlet: "TASS", headline: "Кремль прокомментировал переговоры по энергетике", time: "1分钟前", language: "RU" },
  { countryCode: "DE", countryName: "德国", outlet: "DW", headline: "Bundesregierung kündigt Klimapaket an", time: "2分钟前", language: "DE" },
  { countryCode: "IN", countryName: "印度", outlet: "PTI", headline: "PM Modi addresses parliament on economic agenda", time: "2分钟前", language: "EN" },
  { countryCode: "BR", countryName: "巴西", outlet: "Globo", headline: "Governo anuncia novo pacote de infraestrutura", time: "3分钟前", language: "PT" },
  { countryCode: "KR", countryName: "韩国", outlet: "연합뉴스", headline: "정부, 반도체 산업 지원책 발표", time: "3分钟前", language: "KO" },
  { countryCode: "FR", countryName: "法国", outlet: "AFP", headline: "Le président dévoile la stratégie énergétique", time: "4分钟前", language: "FR" },
  { countryCode: "AU", countryName: "澳大利亚", outlet: "ABC", headline: "PM announces Pacific cooperation initiative", time: "4分钟前", language: "EN" },
  { countryCode: "SA", countryName: "沙特", outlet: "SPA", headline: "الرياض تعلن عن استثمارات جديدة في الطاقة المتجددة", time: "5分钟前", language: "AR" },
  { countryCode: "ZA", countryName: "南非", outlet: "News24", headline: "President outlines economic recovery plan", time: "5分钟前", language: "EN" },
  { countryCode: "TR", countryName: "土耳其", outlet: "Anadolu", headline: "Cumhurbaşkanı yeni dış politika stratejisini açıkladı", time: "6分钟前", language: "TR" },
  { countryCode: "MX", countryName: "墨西哥", outlet: "El Universal", headline: "Presidente presenta plan de desarrollo económico", time: "6分钟前", language: "ES" },
];

/** 热点议题 TOP 10。 */
export interface HotTopic {
  rank: number;
  name: string;
  category: string;
  articleCount24h: number;
  countries: number;
  trend: "up" | "down" | "flat";
  salience: number; // 0-100
}

export const HOT_TOPICS: HotTopic[] = [
  { rank: 1, name: "美联储降息预期升温", category: "经济", articleCount24h: 486, countries: 42, trend: "up", salience: 96 },
  { rank: 2, name: "俄乌冲突新一轮谈判", category: "地缘", articleCount24h: 412, countries: 38, trend: "up", salience: 92 },
  { rank: 3, name: "AI 监管法案全球推进", category: "科技", articleCount24h: 358, countries: 35, trend: "up", salience: 88 },
  { rank: 4, name: "中东局势持续紧张", category: "地缘", articleCount24h: 322, countries: 31, trend: "flat", salience: 82 },
  { rank: 5, name: "全球芯片供应链重组", category: "科技", articleCount24h: 287, countries: 28, trend: "up", salience: 78 },
  { rank: 6, name: "气候变化峰会成果", category: "气候", articleCount24h: 245, countries: 26, trend: "flat", salience: 71 },
  { rank: 7, name: "新兴市场货币波动", category: "经济", articleCount24h: 218, countries: 24, trend: "down", salience: 66 },
  { rank: 8, name: "跨国能源合作新框架", category: "能源", articleCount24h: 192, countries: 21, trend: "up", salience: 62 },
  { rank: 9, name: "全球粮食安全预警", category: "民生", articleCount24h: 167, countries: 19, trend: "flat", salience: 58 },
  { rank: 10, name: "网络安全多边对话", category: "安全", articleCount24h: 143, countries: 17, trend: "up", salience: 54 },
];

/** 人物/机构监测演示:中心人物 + 多层关联实体网络。 */
export interface PersonNode {
  id: string;
  name: string;
  type: "person" | "org" | "country" | "thinktank";
  role?: string;
  mentions: number;
  sentiment?: number; // -1 到 1
}

export interface PersonLink {
  source: string;
  target: string;
  label: string;
}

export const PERSON_DEMO = {
  centerPerson: { name: "某国国务卿", role: "外交首长", mentions: 184 },
  nodes: [
    // 中心
    { id: "center", name: "某国国务卿", type: "person" as const, role: "外交首长", mentions: 184 },

    // 第一层:核心机构与上级
    { id: "org_state", name: "国务院", type: "org" as const, mentions: 156 },
    { id: "org_wh", name: "白宫", type: "org" as const, mentions: 98 },
    { id: "org_nsc", name: "国安委", type: "org" as const, mentions: 67 },
    { id: "org_dod", name: "国防部", type: "org" as const, mentions: 54 },
    { id: "p_president", name: "某国总统", type: "person" as const, mentions: 76 },
    { id: "p_nsadvisor", name: "国安顾问", type: "person" as const, mentions: 58 },
    { id: "p_deputy", name: "副国务卿", type: "person" as const, mentions: 42 },

    // 第二层:智库/研究
    { id: "tt_csis", name: "CSIS", type: "thinktank" as const, mentions: 42 },
    { id: "tt_brookings", name: "布鲁金斯", type: "thinktank" as const, mentions: 38 },
    { id: "tt_cfr", name: "CFR", type: "thinktank" as const, mentions: 31 },
    { id: "tt_rand", name: "兰德公司", type: "thinktank" as const, mentions: 28 },
    { id: "tt_chatham", name: "查塔姆", type: "thinktank" as const, mentions: 19 },

    // 第三层:国家
    { id: "c_cn", name: "中国", type: "country" as const, mentions: 87 },
    { id: "c_ru", name: "俄罗斯", type: "country" as const, mentions: 43 },
    { id: "c_jp", name: "日本", type: "country" as const, mentions: 64 },
    { id: "c_kr", name: "韩国", type: "country" as const, mentions: 52 },
    { id: "c_eu", name: "欧盟", type: "country" as const, mentions: 51 },
    { id: "c_uk", name: "英国", type: "country" as const, mentions: 47 },
    { id: "c_de", name: "德国", type: "country" as const, mentions: 41 },
    { id: "c_in", name: "印度", type: "country" as const, mentions: 36 },
    { id: "c_au", name: "澳大利亚", type: "country" as const, mentions: 33 },

    // 第四层:外方官员
    { id: "fp_cn", name: "某国外长", type: "person" as const, mentions: 58 },
    { id: "fp_ru", name: "俄外长", type: "person" as const, mentions: 39 },
    { id: "fp_jp", name: "日外相", type: "person" as const, mentions: 34 },
    { id: "fp_uk", name: "英外相", type: "person" as const, mentions: 27 },

    // 第五层:国际组织
    { id: "org_un", name: "联合国", type: "org" as const, mentions: 46 },
    { id: "org_nato", name: "北约", type: "org" as const, mentions: 41 },
    { id: "org_g7", name: "G7", type: "org" as const, mentions: 35 },
    { id: "org_quad", name: "四方对话", type: "org" as const, mentions: 29 },
  ] as PersonNode[],
  links: [
    // 中心直连(任职/汇报)
    { source: "center", target: "org_state", label: "任职" },
    { source: "center", target: "p_president", label: "直属上级" },
    { source: "center", target: "p_deputy", label: "直接下属" },
    { source: "center", target: "p_nsadvisor", label: "内阁协作" },
    { source: "center", target: "org_nsc", label: "成员" },

    // 中心 → 智库(公开演讲/引用)
    { source: "center", target: "tt_csis", label: "主题演讲" },
    { source: "center", target: "tt_brookings", label: "对话" },
    { source: "center", target: "tt_cfr", label: "专访" },
    { source: "center", target: "tt_rand", label: "引用观点" },

    // 中心 → 国家(外交互动)
    { source: "center", target: "c_cn", label: "战略竞争" },
    { source: "center", target: "c_ru", label: "对抗" },
    { source: "center", target: "c_jp", label: "同盟协调" },
    { source: "center", target: "c_kr", label: "同盟协调" },
    { source: "center", target: "c_eu", label: "多边合作" },
    { source: "center", target: "c_uk", label: "特殊关系" },
    { source: "center", target: "c_de", label: "跨大西洋" },
    { source: "center", target: "c_in", label: "印太伙伴" },
    { source: "center", target: "c_au", label: "印太同盟" },

    // 中心 → 外方官员(双边会谈)
    { source: "center", target: "fp_cn", label: "双边会谈" },
    { source: "center", target: "fp_ru", label: "对撞" },
    { source: "center", target: "fp_jp", label: "同盟对接" },
    { source: "center", target: "fp_uk", label: "同盟对接" },

    // 中心 → 国际组织
    { source: "center", target: "org_un", label: "多边表态" },
    { source: "center", target: "org_nato", label: "成员协调" },
    { source: "center", target: "org_g7", label: "峰会" },
    { source: "center", target: "org_quad", label: "印太机制" },

    // 跨层:智库 → 国家(研究报告)
    { source: "tt_csis", target: "c_cn", label: "研究报告" },
    { source: "tt_brookings", target: "c_cn", label: "政策分析" },
    { source: "tt_rand", target: "c_ru", label: "兵棋推演" },
    { source: "tt_cfr", target: "c_eu", label: "专题报告" },
    { source: "tt_chatham", target: "c_uk", label: "政策建言" },

    // 跨层:外方官员 → 所属国家
    { source: "fp_cn", target: "c_cn", label: "代表" },
    { source: "fp_ru", target: "c_ru", label: "代表" },
    { source: "fp_jp", target: "c_jp", label: "代表" },
    { source: "fp_uk", target: "c_uk", label: "代表" },

    // 跨层:总统 → 白宫/国安委
    { source: "p_president", target: "org_wh", label: "主持" },
    { source: "p_president", target: "org_nsc", label: "主席" },

    // 跨层:国安顾问 → 国安委/国防部
    { source: "p_nsadvisor", target: "org_nsc", label: "协调" },
    { source: "p_nsadvisor", target: "org_dod", label: "联动" },
    { source: "org_dod", target: "org_nato", label: "军事协作" },

    // 跨层:国家间关系
    { source: "c_jp", target: "c_kr", label: "美日韩机制" },
    { source: "c_jp", target: "c_au", label: "印太协作" },
    { source: "c_uk", target: "c_eu", label: "欧英关系" },
    { source: "c_de", target: "c_eu", label: "欧盟核心" },
    { source: "c_in", target: "c_au", label: "四方成员" },

    // 跨层:国际组织 → 国家
    { source: "org_nato", target: "c_de", label: "成员" },
    { source: "org_nato", target: "c_uk", label: "成员" },
    { source: "org_quad", target: "c_jp", label: "成员" },
    { source: "org_quad", target: "c_in", label: "成员" },
    { source: "org_quad", target: "c_au", label: "成员" },
    { source: "org_g7", target: "c_de", label: "成员" },
    { source: "org_g7", target: "c_jp", label: "成员" },
    { source: "org_g7", target: "c_uk", label: "成员" },

    // 副国务卿 → 国际组织
    { source: "p_deputy", target: "org_un", label: "常驻代表" },
    { source: "p_deputy", target: "org_nato", label: "常驻代表" },
  ] as PersonLink[],
};

/** 智能预警演示:规则 + 触发记录 + 推送样例。 */
export interface AlertRule {
  id: string;
  name: string;
  level: "P1" | "P2" | "P3";
  condition: string;
  status: "triggered" | "watching" | "ok";
  triggeredAt?: string;
  summary?: string;
}

export const ALERT_DEMO: AlertRule[] = [
  {
    id: "rule1",
    name: "中美高层互动异动",
    level: "P1",
    condition: "24h 内中美相关议题报道量超过基线 300%",
    status: "triggered",
    triggeredAt: "14 分钟前",
    summary: "美国务院突发声明,中方 12 家央媒 5 分钟内跟进,日韩媒体 30 分钟内接力报道,信号强度显著",
  },
  {
    id: "rule2",
    name: "俄乌冲突升级信号",
    level: "P2",
    condition: "关键人物表态 + 跟随国 ≥3 国",
    status: "triggered",
    triggeredAt: "1 小时前",
    summary: "俄外长关于核威慑的新表述被 17 国主流媒体引用,英德法同步表态",
  },
  {
    id: "rule3",
    name: "新兴市场货币风险",
    level: "P2",
    condition: "财经议题显著性 7 日移动平均升幅 >50%",
    status: "watching",
    triggeredAt: "观察中",
    summary: "阿根廷/土耳其/埃及货币议题显著性 7 日均值上升 42%,接近阈值",
  },
  {
    id: "rule4",
    name: "全球芯片政策变动",
    level: "P3",
    condition: "半导体相关议题 24h 报道量 >300 篇",
    status: "ok",
    summary: "当前 24h 报道量 287 篇,距阈值 13 篇",
  },
];

/** 预警推送样例(邮件/订阅)。 */
export interface AlertNotification {
  channel: "邮件" | "订阅" | "Webhook";
  target: string;
  sentAt: string;
  status: "sent" | "delivered" | "failed";
}

export const ALERT_NOTIFICATIONS: AlertNotification[] = [
  { channel: "邮件", target: "research@***.gov.cn", sentAt: "14 分钟前", status: "delivered" },
  { channel: "订阅", target: "每日舆情简报", sentAt: "14 分钟前", status: "delivered" },
  { channel: "Webhook", target: "内部 SOC 平台", sentAt: "14 分钟前", status: "sent" },
];
