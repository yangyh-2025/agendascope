# M5 LLM 评估报告（T5.5 LLM 质量评估）

- 任务：docs/dev/4-开发计划.md T5.5（LLM 质量评估）
- 脚本：`scripts/llm_eval.py`
- 报告生成日期：2026-07-28
- **本报告不含任何评估达标数字**：本机无 Docker/PostgreSQL，且 T5.1 回放案例集（`tests/assessment/replay_cases/replay_case_*.json`）由并行任务生成、本机尚不存在；以下为本机实测记录与完整环境执行规程。

## 1. 指标口径（均为估算目标）

| 指标 | 目标 | 数据来源 | 子命令 |
|---|---|---|---|
| 命名盲评均分 | 5 分制 ≥4.0 | `topics` + `llm_judgements`（task_type='topic_naming'）导出盲评 CSV → 人工打分 | `export-naming` / `score-naming` |
| 主题分类宏平均 F1 | ≥0.80 | `llm_judgements`（task_type='topic_category'，predicted）对照回放案例文章 category 标注或人工标注表（真值） | `topic-f1` |
| 归并建议精确率 | ≥85% | 归并留痕对照回放案例 `expected_article_groups` / `expected_separate_pairs` | `merge-precision` |
| 跨语言同事件归并率 | ≥70% | 回放案例 `cross_language_pairs` 对照实际归簇（`topic_articles` 或回放产物 JSON） | `crosslingual` |

## 2. 留痕结构（读代码确认）

- `llm_judgements`（`backend/app/models/llm.py`）：`task_type` 受 CheckConstraint 限制为 `topic_naming / topic_category / topic_summary / first_utterance`；`output_payload = {"value": ...}`，`input_payload = {titles, top_words, name, keywords}`，含 `model_name / prompt_version / success / naming_method / latency_ms`。
- **归并建议不在 llm_judgements**：约束中不存在 `suggested_merge` 类。归并的真实留痕在 `topics.revision_log`（`backend/app/agenda_engine/merge.py`）：`entry.field='merged_into'`、`trigger_evidence.algorithm='nextday_merge'`、`after_value=目标 topic_id`、`actor='machine'`。`merge-precision` 按此结构取数。
- 回放案例与库内文章按 `articles.url` 精确匹配关联；议题归属取 `topic_articles`。

## 3. 脚本用法

```bash
# 1) 命名盲评：导出 CSV → 人工按 5 分制填 score 列 → 统计
.venv/Scripts/python scripts/llm_eval.py export-naming --out docs/dev/reviews/naming_blind_eval.csv
.venv/Scripts/python scripts/llm_eval.py score-naming --scores docs/dev/reviews/naming_blind_eval.csv

# 2) 主题分类宏平均 F1（--labels 可选：人工标注 CSV，topic_id,category）
.venv/Scripts/python scripts/llm_eval.py topic-f1 [--labels labels.csv]

# 3) 归并建议精确率
.venv/Scripts/python scripts/llm_eval.py merge-precision

# 4) 跨语言归并率（--assignments 可选：回放产物 {case_id:{article_id:topic_id}}，缺省查库）
.venv/Scripts/python scripts/llm_eval.py crosslingual [--assignments replay_assignments.json]

# 汇总出报告
.venv/Scripts/python scripts/llm_eval.py all [--scores ...] [--labels ...] [--assignments ...] \
    --out docs/dev/reviews/M5-LLM评估报告.md
```

环境变量：`DATABASE_URL`（默认 `postgresql+psycopg2://agenda:agenda_dev_pwd@localhost:5432/agendascope`）。回放案例目录默认 `tests/assessment/replay_cases/`。
退出码：PASS=0 / FAIL=2 / 环境不可达或无留痕数据=1。

## 4. 本机实测记录（2026-07-28，Windows 11 + Git Bash，无 Docker）

### 4.1 `all` 汇总（DB 不可达 + 无回放案例）

```text
$ .venv/Scripts/python scripts/llm_eval.py all
全部指标无留痕数据，无法出具评估结论
EXIT=1
```

（各指标分别尝试连接 DB / 加载案例集，均落入 NO_DATA；脚本拒绝在零数据下出报告。）

### 4.2 `export-naming`（依赖 DB）

```text
$ .venv/Scripts/python scripts/llm_eval.py export-naming
[环境不可达] PostgreSQL 不可达（DATABASE_URL=postgresql+psycopg2://agenda:agenda_dev_pwd@localhost:5432/agendascope）: (psycopg2.OperationalError) connection to server at "localhost", port 5432 failed: Connection refused (0x0000274D/10061)
...
EXIT=1
```

### 4.3 `topic-f1`（依赖 DB + 回放案例）

```text
$ .venv/Scripts/python scripts/llm_eval.py topic-f1
[环境不可达] PostgreSQL 不可达（DATABASE_URL=postgresql+psycopg2://...）: connection refused (0x0000274D/10061)
EXIT=1
```

### 4.4 `score-naming`（离线可运行，机制验证）

`score-naming` 不依赖 DB，可离线跑通。用临时机制验证 CSV（非评估数据，仅验证统计与退出码逻辑）：

```text
$ .venv/Scripts/python scripts/llm_eval.py score-naming --scores /tmp/naming_mechanics_check.csv   # 2/3 行打分 5、4
命名盲评：2/3 条已评分，均分 4.5（目标 ≥4.0）→ PASS
EXIT=0

$ .venv/Scripts/python scripts/llm_eval.py score-naming --scores /tmp/naming_mechanics_fail.csv    # 1 行打分 2
命名盲评：1/1 条已评分，均分 2.0（目标 ≥4.0）→ FAIL
EXIT=2

$ .venv/Scripts/python scripts/llm_eval.py score-naming --scores docs/dev/reviews/naming_blind_eval.csv  # 文件尚不存在
[无数据] 打分文件不存在：docs/dev/reviews/naming_blind_eval.csv（需先 export-naming 并人工打分）
EXIT=1
```

均分计算、空行跳过、PASS=0 / FAIL=2 / 无数据=1 三条路径均符合预期。验证用临时文件已删除，不构成评估数据。

### 4.5 语法校验

```text
$ .venv/Scripts/python -m py_compile scripts/stress_test.py scripts/llm_eval.py
COMPILE_OK
```

## 5. 完整环境下的执行步骤与达标判定

前置：PostgreSQL 可达且已迁移；管线已产出真实议题与 LLM 判定留痕；T5.1 回放案例集已生成至 `tests/assessment/replay_cases/` 并完成回放（文章已按 url 入库并入簇）。

1. `export-naming` 导出盲评 CSV → 至少 2 名标注者按 5 分制独立打分 → `score-naming` 统计均分，**≥4.0 达标**。
2. `topic-f1`（案例文章携带 category 标注时自动取真值，否则提供 `--labels` 人工标注表）逐类输出 P/R/F1，**宏平均 F1 ≥0.80 达标**。
3. `merge-precision` 对照案例 `expected_article_groups` / `expected_separate_pairs` 判定每条机器归并正误，**精确率 ≥85% 达标**；无法对照案例验证的归并单独计数、不计入分母，报告中如实披露。
4. `crosslingual` 对 `cross_language_pairs` 统计归入同一议题比例，**≥70% 达标**；不达标时按 2.2 节预案评估向量模型（预留 LaBSE 切换，ADR-005）。
5. `all` 汇总四项出最终报告；任一 FAIL 输出不达标明细；全部 NO_DATA 时拒绝出结论（退出码 1）。

## 6. 结论

本机环境受限（无 DB、回放案例集未落位），T5.5 四项指标**均未实测，无达标/不达标结论**。脚本已经过语法校验、环境不可达路径与离线子命令（`score-naming`）机制实测，待 T5.1 案例集与数据库就绪后按第 5 节执行并回填真实数字。
