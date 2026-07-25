/**
 * 30 国常量（PRD 1.3 / 5.2 媒体源目标国集）。
 * 用于议题/事件筛选下拉，避免硬编码到具体页面。
 */
export interface CountryOption {
  code: string;
  label: string;
}

export const COUNTRIES: CountryOption[] = [
  { code: "CN", label: "中国" },
  { code: "US", label: "美国" },
  { code: "GB", label: "英国" },
  { code: "FR", label: "法国" },
  { code: "DE", label: "德国" },
  { code: "RU", label: "俄罗斯" },
  { code: "JP", label: "日本" },
  { code: "KR", label: "韩国" },
  { code: "IN", label: "印度" },
  { code: "AU", label: "澳大利亚" },
  { code: "CA", label: "加拿大" },
  { code: "BR", label: "巴西" },
  { code: "MX", label: "墨西哥" },
  { code: "AR", label: "阿根廷" },
  { code: "ZA", label: "南非" },
  { code: "EG", label: "埃及" },
  { code: "NG", label: "尼日利亚" },
  { code: "KE", label: "肯尼亚" },
  { code: "SA", label: "沙特阿拉伯" },
  { code: "AE", label: "阿联酋" },
  { code: "IR", label: "伊朗" },
  { code: "TR", label: "土耳其" },
  { code: "IL", label: "以色列" },
  { code: "PK", label: "巴基斯坦" },
  { code: "ID", label: "印度尼西亚" },
  { code: "MY", label: "马来西亚" },
  { code: "SG", label: "新加坡" },
  { code: "TH", label: "泰国" },
  { code: "VN", label: "越南" },
  { code: "PH", label: "菲律宾" },
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
