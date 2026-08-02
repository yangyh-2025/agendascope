/**
 * 全球监控目标国常量（G20 + 全球南方 + 主要经济体，单一事实源：backend/app/core/countries.py）。
 * 用于议题/事件筛选下拉，避免硬编码到具体页面。底层存 ISO 两位码，展示中文名。
 */
export interface CountryOption {
  code: string;
  label: string;
}

export const COUNTRIES: CountryOption[] = [
  // 东亚
  { code: "CN", label: "中国" },
  { code: "JP", label: "日本" },
  { code: "KR", label: "韩国" },
  // 东南亚
  { code: "ID", label: "印度尼西亚" },
  { code: "MY", label: "马来西亚" },
  { code: "SG", label: "新加坡" },
  { code: "TH", label: "泰国" },
  { code: "VN", label: "越南" },
  { code: "PH", label: "菲律宾" },
  { code: "MM", label: "缅甸" },
  { code: "KH", label: "柬埔寨" },
  { code: "LA", label: "老挝" },
  { code: "BN", label: "文莱" },
  // 南亚
  { code: "IN", label: "印度" },
  { code: "PK", label: "巴基斯坦" },
  { code: "BD", label: "孟加拉国" },
  { code: "LK", label: "斯里兰卡" },
  { code: "NP", label: "尼泊尔" },
  { code: "AF", label: "阿富汗" },
  // 中东
  { code: "SA", label: "沙特阿拉伯" },
  { code: "AE", label: "阿联酋" },
  { code: "QA", label: "卡塔尔" },
  { code: "IR", label: "伊朗" },
  { code: "IL", label: "以色列" },
  { code: "TR", label: "土耳其" },
  { code: "KW", label: "科威特" },
  { code: "JO", label: "约旦" },
  { code: "LB", label: "黎巴嫩" },
  { code: "IQ", label: "伊拉克" },
  { code: "SY", label: "叙利亚" },
  { code: "YE", label: "也门" },
  { code: "BH", label: "巴林" },
  { code: "OM", label: "阿曼" },
  { code: "PS", label: "巴勒斯坦" },
  // 欧洲
  { code: "GB", label: "英国" },
  { code: "DE", label: "德国" },
  { code: "FR", label: "法国" },
  { code: "IT", label: "意大利" },
  { code: "ES", label: "西班牙" },
  { code: "RU", label: "俄罗斯" },
  { code: "PL", label: "波兰" },
  { code: "SE", label: "瑞典" },
  { code: "NO", label: "挪威" },
  { code: "CH", label: "瑞士" },
  { code: "NL", label: "荷兰" },
  { code: "BE", label: "比利时" },
  { code: "GR", label: "希腊" },
  { code: "PT", label: "葡萄牙" },
  { code: "FI", label: "芬兰" },
  { code: "DK", label: "丹麦" },
  { code: "CZ", label: "捷克" },
  { code: "AT", label: "奥地利" },
  { code: "IE", label: "爱尔兰" },
  { code: "UA", label: "乌克兰" },
  { code: "HU", label: "匈牙利" },
  { code: "RO", label: "罗马尼亚" },
  { code: "BG", label: "保加利亚" },
  { code: "SK", label: "斯洛伐克" },
  // 北美
  { code: "US", label: "美国" },
  { code: "CA", label: "加拿大" },
  // 拉美
  { code: "BR", label: "巴西" },
  { code: "MX", label: "墨西哥" },
  { code: "AR", label: "阿根廷" },
  { code: "CL", label: "智利" },
  { code: "CO", label: "哥伦比亚" },
  { code: "PE", label: "秘鲁" },
  { code: "UY", label: "乌拉圭" },
  { code: "BO", label: "玻利维亚" },
  { code: "EC", label: "厄瓜多尔" },
  { code: "VE", label: "委内瑞拉" },
  { code: "PY", label: "巴拉圭" },
  { code: "CU", label: "古巴" },
  { code: "DO", label: "多米尼加" },
  // 大洋洲
  { code: "AU", label: "澳大利亚" },
  { code: "NZ", label: "新西兰" },
  { code: "FJ", label: "斐济" },
  // 非洲
  { code: "ZA", label: "南非" },
  { code: "EG", label: "埃及" },
  { code: "NG", label: "尼日利亚" },
  { code: "KE", label: "肯尼亚" },
  { code: "ET", label: "埃塞俄比亚" },
  { code: "MA", label: "摩洛哥" },
  { code: "GH", label: "加纳" },
  { code: "TZ", label: "坦桑尼亚" },
  { code: "UG", label: "乌干达" },
  { code: "DZ", label: "阿尔及利亚" },
  { code: "TN", label: "突尼斯" },
  { code: "LY", label: "利比亚" },
  { code: "RW", label: "卢旺达" },
  { code: "SN", label: "塞内加尔" },
  { code: "CI", label: "科特迪瓦" },
  { code: "CM", label: "喀麦隆" },
  { code: "AO", label: "安哥拉" },
  { code: "MZ", label: "莫桑比克" },
  { code: "ZM", label: "赞比亚" },
  { code: "ZW", label: "津巴布韦" },
  { code: "BW", label: "博茨瓦纳" },
  { code: "GA", label: "加蓬" },
  { code: "CD", label: "刚果(金)" },
  // 中亚
  { code: "KZ", label: "哈萨克斯坦" },
  { code: "UZ", label: "乌兹别克斯坦" },
  { code: "TM", label: "土库曼斯坦" },
  { code: "KG", label: "吉尔吉斯斯坦" },
  { code: "TJ", label: "塔吉克斯坦" },
  { code: "AZ", label: "阿塞拜疆" },
  { code: "GE", label: "格鲁吉亚" },
  { code: "AM", label: "亚美尼亚" },
  { code: "BY", label: "白俄罗斯" },
];

const COUNTRY_LABEL_MAP = new Map(COUNTRIES.map((c) => [c.code, c.label]));

/** 取国家中文名，未收录时回退为原始代码。 */
export function countryLabel(code: string | null | undefined): string {
  if (!code) return "—";
  return COUNTRY_LABEL_MAP.get(code) ?? code;
}

/** 议题主题分类（PRD 4.4 主题分类枚举）。 */
export const TOPIC_CATEGORIES: string[] = [
  "政治安全",
  "经济金融",
  "军事",
  "科技",
  "能源气候",
  "社会民生",
  "其他",
];

/** 议题生命周期中文标签。 */
export const LIFECYCLE_LABEL: Record<string, string> = {
  nascent: "萌芽",
  forming: "形成中",
  confirmed: "已确认",
  evolving: "演化中",
  archived: "已归档",
};

/** 议程设置事件状态中文标签。 */
export const AGENDA_EVENT_STATUS_LABEL: Record<string, string> = {
  watching: "观察中",
  suspected: "疑似",
  confirmed: "已确认",
  dismissed: "已排除",
  revised: "已修正",
  archived: "已归档",
};
