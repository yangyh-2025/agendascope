/** 数据开放平台 API 文档（静态内容）。 */

export const OPEN_API_BASE = "/api/v1/open";

export interface DocSection {
  id: string;
  title: string;
  description: string;
  endpoints: DocEndpoint[];
}

export interface DocEndpoint {
  method: "GET";
  path: string;
  summary: string;
  params?: { name: string; type: string; required?: boolean; desc: string }[];
  example?: string;
}

export const DOC_SECTIONS: DocSection[] = [
  {
    id: "auth",
    title: "鉴权",
    description:
      "所有 /api/v1/open/* 接口都需要在请求头携带 X-API-Key。Key 由登录用户在「我的 Key」页生成。" +
      "每个 Key 有独立的每分钟限流，超限返回 429。",
    endpoints: [
      {
        method: "GET",
        path: "(请求头)",
        summary: "X-API-Key: agk_xxxxxxxx",
        example: `curl -H "X-API-Key: agk_xxxxxxxxxxxx" \\
  https://www.wordread.cn/api/v1/open/topics?page=1&page_size=10`,
      },
    ],
  },
  {
    id: "topics",
    title: "议题",
    description: "平台聚类出的舆情议题。每个议题含名称、关键词、显著性、生命周期等元数据。",
    endpoints: [
      {
        method: "GET",
        path: "/topics",
        summary: "议题列表",
        params: [
          { name: "status", type: "string", desc: "emerging/heating/stable/declining/archived" },
          { name: "category", type: "string", desc: "议题分类（政治安全/经济金融/军事/科技/能源气候/社会民生/其他）" },
          { name: "q", type: "string", desc: "议题名模糊匹配" },
          { name: "page", type: "int", desc: "页码（默认 1）" },
          { name: "page_size", type: "int", desc: "每页条数（默认 20，最大 100）" },
        ],
        example: `curl -H "X-API-Key: $KEY" \\
  "https://www.wordread.cn/api/v1/open/topics?status=heating&page_size=10"`,
      },
      {
        method: "GET",
        path: "/topics/{id}",
        summary: "议题详情",
      },
    ],
  },
  {
    id: "articles",
    title: "文章",
    description: "172 国 408 个媒体源的实时采集文章，含标题、正文、情感、国家、源等元数据。",
    endpoints: [
      {
        method: "GET",
        path: "/articles",
        summary: "文章列表（默认最近 24 小时）",
        params: [
          { name: "country_code", type: "string", desc: "ISO 两位国家码" },
          { name: "topic_id", type: "uuid", desc: "议题 ID 过滤" },
          { name: "source_id", type: "uuid", desc: "媒体源 ID 过滤" },
          { name: "language", type: "string", desc: "语言码（en/zh/ja/ar 等）" },
          { name: "hours", type: "int", desc: "最近 N 小时（默认 24，最大 720）" },
          { name: "q", type: "string", desc: "标题模糊匹配" },
        ],
        example: `curl -H "X-API-Key: $KEY" \\
  "https://www.wordread.cn/api/v1/open/articles?country_code=US&hours=48"`,
      },
      {
        method: "GET",
        path: "/articles/{id}",
        summary: "文章详情（含完整正文）",
        params: [
          { name: "include_content", type: "bool", desc: "默认 true，false 则只返回元数据" },
        ],
      },
    ],
  },
  {
    id: "entities",
    title: "监控对象",
    description: "精品 50 个关键个人与机构实体库 + NER 自动登记的外围实体。",
    endpoints: [
      {
        method: "GET",
        path: "/entities",
        summary: "实体列表",
        params: [
          { name: "entity_type", type: "string", desc: "person/thinktank/intl_org/gov_body" },
          { name: "country_code", type: "string", desc: "ISO 两位国家码" },
          { name: "monitored", type: "bool", desc: "true 仅看重点监测" },
        ],
      },
      {
        method: "GET",
        path: "/entities/{id}",
        summary: "实体详情",
      },
    ],
  },
  {
    id: "agenda-events",
    title: "议程事件",
    description: "系统识别出的潜在议程设置事件（首发→跟随→跨境传播链路）。",
    endpoints: [
      {
        method: "GET",
        path: "/agenda-events",
        summary: "议程事件列表",
        params: [
          { name: "country_code", type: "string", desc: "首发国家码" },
          { name: "status", type: "string", desc: "watching/suspected/confirmed/dismissed/revised/archived" },
          { name: "days", type: "int", desc: "最近 N 天（默认 30）" },
        ],
      },
    ],
  },
  {
    id: "sources",
    title: "媒体源",
    description: "平台监控的 172 国 408 个媒体源元数据。",
    endpoints: [
      {
        method: "GET",
        path: "/sources",
        summary: "媒体源列表",
        params: [
          { name: "country_code", type: "string", desc: "国家码" },
          { name: "status", type: "string", desc: "active/degraded/failed" },
        ],
      },
      {
        method: "GET",
        path: "/countries",
        summary: "国家元数据（ISO 码 + 中文名）",
      },
    ],
  },
  {
    id: "snapshots",
    title: "显著性快照",
    description: "议题在国家×时间窗的显著性得分与排名（驱动热点 TOP10 与预警）。",
    endpoints: [
      {
        method: "GET",
        path: "/snapshots",
        summary: "显著性快照",
        params: [
          { name: "topic_id", type: "uuid", desc: "议题 ID" },
          { name: "country_code", type: "string", desc: "国家码" },
          { name: "granularity", type: "string", desc: "hour/day/week（默认 day）" },
          { name: "days", type: "int", desc: "最近 N 天（默认 7，最大 90）" },
        ],
      },
    ],
  },
];

export const ERROR_CODES_DOC = `
统一响应结构：{ code, data, message }

错误码：
  0     成功
  2001  未认证（缺 X-API-Key 或 Key 无效/吊销/过期）
  3001  资源不存在
  5001  超出限流（每分钟 Key 配额）
  9001  服务器内部错误
`;
