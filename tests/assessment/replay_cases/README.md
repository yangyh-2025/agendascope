# 回放测试标注案例集（Replay Cases）

本目录存放 AgendaScope「观澜」平台议程设置识别算法的回放测试标注案例（Phase 5 T5.1）。
每个 `replay_case_<case_id>.json` 为一个独立测试案例，可整包回放到采集→聚类→判定链路，
用 `ground_truth` 中的标注校验系统输出。

## 案例来源

全部案例基于**公开历史事件**构造，属标注测试数据，不对应任何真实抓取记录：

- **事件与时间线**：选取 2018–2023 年间具有清晰首发-跟随传播链的国际公共事件
  （如新疆棉/BCI 声明、孟晚舟被捕、卡舒吉案、苏伊士运河堵塞、纳瓦利内中毒、贝鲁特大爆炸、
  俄乌战争爆发、福岛核处理水排海、Pegasus 间谍软件曝光、潘多拉文件、AUKUS 协议、
  北溪管道爆炸、玛莎·阿米尼事件、硅谷银行倒闭、土耳其-叙利亚地震、COP26、
  欧盟《人工智能法案》、TikTok 禁令争议、智利社会抗议、WHO PHEIC 宣布等）。
  首发国、首发媒体与各国跟进的先后顺序参照公开报道时间线设定。
- **文章文本**：标题与正文为依据所声明语言撰写的新闻风格短文（2–4 句），
  内容与公开史实一致，但并非任何真实报道的原文。
- **URL**：使用该媒体真实域名 + 虚构路径，仅用于格式仿真，不可访问。

## 构造方法

每个案例包含 8–14 篇文章，按以下规则构造：

1. **首发文章 1 篇**：`country_code` 与 `source_name` 分别等于 `ground_truth.origin_country`
   与 `ground_truth.origin_source_name`，其 `published_at` 为全案例最早，且等于
   `ground_truth.origin_at`。`time_source` 绝大多数为 `feed`；低置信首发负例使用 `crawled`。
2. **跟随文章 ≥3 国**：`ground_truth.follower_sequence` 按 `lag_hours` 升序列出跟随国家，
   每个跟随国的首篇文章 `published_at = origin_at + lag_hours`（误差 ≤1 小时）；
   正例要求跟随国 ≥3 且 `lag_hours ≤ 336`（14 天）。同一国家允许多篇文章。
3. **跨语言报道对**：多数正例含 1–2 对不同语言、同一事件的报道对，两端文章均放入
   `expected_article_groups` 主组，并登记在 `cross_language_pairs`，用于跨语言归并率检测。
4. **干扰文章 2–3 篇**：`article_id` 以 `d` 开头，内容为完全不同主题的短新闻，
   不进入 `expected_article_groups` 主组，并与主组文章登记 ≥2 个
   `expected_separate_pairs`，用于误并（不应同议题却同议题）检测。

## 正例与负例

- **正例（`should_be_agenda_event = true`，20 个）**：满足首发源可信（feed 时间戳）+
  ≥3 国 14 天内跟随的传播链。其中 19 个案例含非空 `cross_language_pairs`，
  超过 10 个案例为 5 国以上的明显跨国跟随链。
- **负例（`should_be_agenda_event = false`，4 个）**，各对应一类不成立条件：
  - `neg-independent-regional-floods-2023`：多国同日各自报道本国独立洪灾，
    属多国独立自发报道，应拆不并（`expected_separate_pairs` 覆盖全部事件文章两两组合）；
  - `neg-nz-housing-policy-2023`：除首发国外仅 1 国跟随（b 条件：≥3 国不满足）；
  - `neg-crawled-rumor-resignation-2022`：首发源 `time_source = crawled`
    （a 条件：首发时间置信度不足，不应自动成立）；
  - `neg-german-state-election-2021`：仅首发国本国报道，`follower_sequence` 为空。

## 字段约定

- `article_id`：案例内唯一，事件相关文章用 `a1…a9`，干扰文章用 `d1…d3`。
- `source_media_type` ∈ `{newspaper, agency, broadcast, online}`；通讯社
  （Reuters / AP / AFP / 新华社 / TASS / Bloomberg / Anadolu）一律为 `agency`。
- `time_source` ∈ `{feed, crawled, gdelt}`，除首发低置信负例外一律 `feed`。
- `language` ∈ `{zh, en, ar, ru, fr, es, ja, de}`，与正文实际语言一致。
- `country_code` 为 ISO-3166 alpha-2，按媒体所属国标注
  （如 Reuters/BBC→GB，AFP/Le Monde→FR，TASS→RU，Al Jazeera Arabic→QA）。
- 所有时间戳为 ISO 8601 并带 `+00:00` 时区。

## 使用方式

将 `articles` 按 `published_at` 升序注入回放管道后，校验：

1. 议程设置判定结果与 `should_be_agenda_event` 一致；
2. 识别出的首发源/首发时间与 `origin_source_name` / `origin_at` 一致；
3. 跟随国家顺序与 `follower_sequence` 一致（lag 误差 ≤1h）；
4. `expected_article_groups` 同组文章归入同一议题；
5. `expected_separate_pairs` 中的文章对不归入同一议题；
6. `cross_language_pairs` 中的文章成功跨语言归并。
