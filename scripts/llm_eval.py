"""T5.5 LLM 质量评估（AgendaScope 观澜 Phase 5）。

四项指标（docs/dev/4-开发计划.md T5.5，均为估算目标）：
  1. 命名盲评 5 分制均分 ≥4.0（export-naming 导出盲评 CSV → 人工打分 → score-naming 统计）
  2. 主题分类宏平均 F1 ≥0.80（topic-f1）
  3. 归并建议精确率 ≥85%（merge-precision）
  4. 跨语言同事件归并率 ≥70%（crosslingual）

留痕结构（读代码确认）：
  - llm_judgements（backend/app/models/llm.py）：task_type ∈
    topic_naming/topic_category/topic_summary/first_utterance（CheckConstraint），
    output_payload = {"value": ...}，input_payload = {titles, top_words, name, keywords}，
    含 model_name/prompt_version/success/naming_method/latency_ms。
    注意：约束中不存在 suggested_merge 类——归并建议的真实留痕在
    topics.revision_log（backend/app/agenda_engine/merge.py）：
    entry.field='merged_into'，trigger_evidence.algorithm='nextday_merge'，
    after_value=目标 topic_id，actor='machine'。
  - topics.topic_category 为最近分类结果；topic_articles 为议题-文章归属。
  - 回放案例与库内文章按 articles.url 精确匹配关联。

退出码：PASS=0 / FAIL=2 / 环境不可达或无留痕数据=1（显式报错，禁止编造数字）。
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
from datetime import UTC, datetime

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(ROOT_DIR, "backend")
sys.path.insert(0, BACKEND_DIR)  # 与 tests/conftest.py 一致，可 import app 包

DEFAULT_DATABASE_URL = "postgresql+psycopg2://agenda:agenda_dev_pwd@localhost:5432/agendascope"
DEFAULT_CASES_DIR = os.path.join("tests", "assessment", "replay_cases")
DEFAULT_REPORT = os.path.join("docs", "dev", "reviews", "M5-LLM评估报告.md")

TARGET_NAMING = 4.0
TARGET_F1 = 0.80
TARGET_MERGE_PRECISION = 0.85
TARGET_CROSSLINGUAL = 0.70


class EnvError(RuntimeError):
    """环境不可达，退出码 1。"""


class NoDataError(RuntimeError):
    """无留痕/无案例数据，退出码 1。"""


def connect_db():
    try:
        from sqlalchemy import create_engine, text
    except ImportError as exc:
        raise EnvError(f"缺少依赖 sqlalchemy（请用项目 .venv 运行）: {exc}") from exc
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    try:
        engine = create_engine(url, connect_args={"connect_timeout": 5})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        raise EnvError(f"PostgreSQL 不可达（DATABASE_URL={url}）: {exc}") from exc
    return engine


def load_replay_cases(cases_dir: str) -> list[dict]:
    """加载回放案例集（tests/assessment/replay_cases/replay_case_*.json）。"""
    pattern = os.path.join(cases_dir, "replay_case_*.json")
    paths = sorted(glob.glob(pattern))
    if not paths:
        raise NoDataError(f"无回放案例数据：{pattern} 未匹配到文件（T5.1 回放测试集尚未入库？）")
    cases = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            cases.append(json.load(f))
    return cases


def case_url_index(cases: list[dict]) -> dict[str, tuple[dict, dict]]:
    """url -> (case, article) 索引；案例文章按 url 与库内 articles 关联。"""
    index: dict[str, tuple[dict, dict]] = {}
    for case in cases:
        for art in case.get("articles", []):
            url = art.get("url")
            if url:
                index[url] = (case, art)
    return index


def expected_group_pairs(case: dict) -> set[frozenset]:
    gt = case.get("ground_truth", {})
    return {frozenset(pair) for pair in gt.get("expected_article_groups", []) if len(pair) == 2}


def separate_pairs(case: dict) -> set[frozenset]:
    gt = case.get("ground_truth", {})
    return {frozenset(pair) for pair in gt.get("expected_separate_pairs", []) if len(pair) == 2}


# ---------------------------------------------------------------------------
# 1. 命名盲评
# ---------------------------------------------------------------------------
NAMING_CSV_FIELDS = [
    "topic_id", "name_auto", "naming_method", "model_name", "prompt_version",
    "evidence_titles", "keywords", "score",
]


def cmd_export_naming(args: argparse.Namespace) -> int:
    from sqlalchemy import text

    engine = connect_db()
    sql = """
        SELECT DISTINCT ON (j.topic_id)
               j.topic_id, t.name_auto, t.naming_method, j.model_name, j.prompt_version,
               j.input_payload
        FROM llm_judgements j
        JOIN topics t ON t.id = j.topic_id
        WHERE j.task_type = 'topic_naming' AND j.success
        ORDER BY j.topic_id, j.created_at DESC
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql)).mappings().all()
    if not rows:
        print("无留痕数据：llm_judgements 中没有成功的 topic_naming 判定", file=sys.stderr)
        return 1
    rows = rows[: args.limit]
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=NAMING_CSV_FIELDS)
        writer.writeheader()
        for r in rows:
            payload = r["input_payload"] or {}
            titles = payload.get("titles") or []
            writer.writerow({
                "topic_id": str(r["topic_id"]),
                "name_auto": r["name_auto"],
                "naming_method": r["naming_method"],
                "model_name": r["model_name"],
                "prompt_version": r["prompt_version"],
                "evidence_titles": " | ".join(titles[:10]),
                "keywords": "、".join(payload.get("top_words") or []),
                "score": "",
            })
    print(f"已导出 {len(rows)} 条命名盲评样本到 {args.out}；请人工按 5 分制填写 score 列后用 score-naming 统计")
    return 0


def score_naming(csv_path: str) -> dict:
    """读取人工打分 CSV，返回统计（不依赖 DB，可离线运行）。"""
    if not os.path.exists(csv_path):
        raise NoDataError(f"打分文件不存在：{csv_path}（需先 export-naming 并人工打分）")
    scores: list[float] = []
    total = 0
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            total += 1
            raw = (row.get("score") or "").strip()
            if not raw:
                continue
            try:
                v = float(raw)
            except ValueError:
                continue
            if 1.0 <= v <= 5.0:
                scores.append(v)
    if not scores:
        raise NoDataError(f"打分文件 {csv_path} 共 {total} 行但 score 列全部为空，无人工打分数据")
    avg = sum(scores) / len(scores)
    return {
        "scored": len(scores), "total_rows": total,
        "avg": round(avg, 3), "target": TARGET_NAMING,
        "pass": avg >= TARGET_NAMING,
    }


def cmd_score_naming(args: argparse.Namespace) -> int:
    try:
        result = score_naming(args.scores)
    except NoDataError as exc:
        print(f"[无数据] {exc}", file=sys.stderr)
        return 1
    verdict = "PASS" if result["pass"] else "FAIL"
    print(
        f"命名盲评：{result['scored']}/{result['total_rows']} 条已评分，"
        f"均分 {result['avg']}（目标 ≥{TARGET_NAMING}）→ {verdict}"
    )
    return 0 if result["pass"] else 2


# ---------------------------------------------------------------------------
# 2. 主题分类宏平均 F1
# ---------------------------------------------------------------------------
def load_manual_labels(path: str) -> dict[str, str]:
    """人工标注表 CSV：topic_id,category。"""
    labels: dict[str, str] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            tid = (row.get("topic_id") or "").strip()
            cat = (row.get("category") or "").strip()
            if tid and cat:
                labels[tid] = cat
    return labels


def case_truth_by_url(cases: list[dict]) -> dict[str, str]:
    """从回放案例提取 url -> 真值类别（文章级 category 标注，或案例级 expected_category）。"""
    truth: dict[str, str] = {}
    for case in cases:
        case_cat = case.get("ground_truth", {}).get("expected_category") or case.get("expected_category")
        for art in case.get("articles", []):
            cat = art.get("category") or case_cat
            if art.get("url") and cat:
                truth[art["url"]] = cat
    return truth


def cmd_topic_f1(args: argparse.Namespace) -> int:
    from sqlalchemy import text

    engine = connect_db()
    try:
        cases = load_replay_cases(args.cases_dir)
    except NoDataError as exc:
        if not args.labels:
            print(f"[无数据] {exc}", file=sys.stderr)
            return 1
        cases = []

    # 预测值：每议题最新一条成功的 topic_category 判定
    pred_sql = """
        SELECT DISTINCT ON (j.topic_id) j.topic_id, j.output_payload ->> 'value' AS predicted
        FROM llm_judgements j
        WHERE j.task_type = 'topic_category' AND j.success
        ORDER BY j.topic_id, j.created_at DESC
    """
    with engine.connect() as conn:
        predicted = {str(r.topic_id): r.predicted for r in conn.execute(text(pred_sql))}
    if not predicted:
        print("无留痕数据：llm_judgements 中没有成功的 topic_category 判定", file=sys.stderr)
        return 1

    # 真值来源①：人工标注表；来源②：回放案例 url→category 经 topic_articles 关联到议题
    truth_by_topic: dict[str, str] = {}
    if args.labels:
        truth_by_topic.update(load_manual_labels(args.labels))
    if cases:
        url_truth = case_truth_by_url(cases)
        if url_truth:
            map_sql = """
                SELECT ta.topic_id, a.url
                FROM topic_articles ta JOIN articles a ON a.id = ta.article_id
                WHERE a.url = ANY(:urls)
            """
            with engine.connect() as conn:
                rows = conn.execute(text(map_sql), {"urls": list(url_truth)}).all()
            votes: dict[str, dict[str, int]] = {}
            for topic_id, url in rows:
                cat = url_truth.get(url)
                if not cat:
                    continue
                votes.setdefault(str(topic_id), {})[cat] = votes.setdefault(str(topic_id), {}).get(cat, 0) + 1
            for tid, counts in votes.items():
                truth_by_topic.setdefault(tid, max(counts, key=counts.get))

    pairs = [(tid, truth_by_topic[tid], predicted[tid]) for tid in truth_by_topic if tid in predicted]
    if not pairs:
        print(
            "无可用真值：回放案例未携带 category 标注且未提供 --labels 人工标注表，"
            "或案例文章 url 与库内 topic_articles 无交集",
            file=sys.stderr,
        )
        return 1

    labels = sorted({t for _, t, _ in pairs} | {p for _, _, p in pairs})
    per_class = []
    f1_sum = 0.0
    for lab in labels:
        tp = sum(1 for _, t, p in pairs if t == lab and p == lab)
        fp = sum(1 for _, t, p in pairs if t != lab and p == lab)
        fn = sum(1 for _, t, p in pairs if t == lab and p != lab)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        f1_sum += f1
        per_class.append({"label": lab, "support": sum(1 for _, t, _ in pairs if t == lab),
                          "precision": round(prec, 3), "recall": round(rec, 3), "f1": round(f1, 3)})
    macro_f1 = f1_sum / len(labels)
    ok = macro_f1 >= TARGET_F1
    print(f"主题分类：{len(pairs)} 个议题有真值+预测，宏平均 F1 = {macro_f1:.3f}（目标 ≥{TARGET_F1}）→ {'PASS' if ok else 'FAIL'}")
    for c in per_class:
        print(f"  {c['label']}: support={c['support']} P={c['precision']} R={c['recall']} F1={c['f1']}")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({"macro_f1": round(macro_f1, 4), "n": len(pairs), "per_class": per_class,
                       "pass": ok}, f, ensure_ascii=False, indent=2)
    return 0 if ok else 2


# ---------------------------------------------------------------------------
# 3. 归并建议精确率
# ---------------------------------------------------------------------------
def cmd_merge_precision(args: argparse.Namespace) -> int:
    from sqlalchemy import text

    engine = connect_db()
    try:
        cases = load_replay_cases(args.cases_dir)
    except NoDataError as exc:
        print(f"[无数据] {exc}", file=sys.stderr)
        return 1

    # 归并建议留痕：topics.revision_log 中 field='merged_into' 且 algorithm='nextday_merge'
    # （llm_judgements 的 CheckConstraint 不含 suggested_merge，真实留痕在 revision_log）
    merge_sql = """
        SELECT t.id AS source_topic_id, e.entry ->> 'after_value' AS target_topic_id
        FROM topics t, LATERAL jsonb_array_elements(t.revision_log) AS e(entry)
        WHERE e.entry ->> 'field' = 'merged_into'
          AND e.entry -> 'trigger_evidence' ->> 'algorithm' = 'nextday_merge'
          AND e.entry ->> 'actor' = 'machine'
    """
    with engine.connect() as conn:
        merges = [(str(r.source_topic_id), str(r.target_topic_id)) for r in conn.execute(text(merge_sql))]
    if not merges:
        print("无留痕数据：topics.revision_log 中没有 nextday_merge 机器归并记录", file=sys.stderr)
        return 1

    url_index = case_url_index(cases)
    map_sql = """
        SELECT ta.topic_id, a.url
        FROM topic_articles ta JOIN articles a ON a.id = ta.article_id
        WHERE a.url = ANY(:urls)
    """
    with engine.connect() as conn:
        rows = conn.execute(text(map_sql), {"urls": list(url_index)}).all()
    topic_case_articles: dict[str, set[str]] = {}
    art_case: dict[str, str] = {}
    for _url, (case, art) in url_index.items():
        art_case[art["article_id"]] = case["case_id"]
    for topic_id, url in rows:
        case, art = url_index[url]
        topic_case_articles.setdefault(str(topic_id), set()).add(art["article_id"])

    correct = wrong = unverifiable = 0
    details = []
    for source_id, target_id in merges:
        src_arts = topic_case_articles.get(source_id, set())
        tgt_arts = topic_case_articles.get(target_id, set())
        verdict = None
        for case in cases:
            cid = case["case_id"]
            s_in = {a for a in src_arts if art_case.get(a) == cid}
            t_in = {a for a in tgt_arts if art_case.get(a) == cid}
            if not s_in or not t_in:
                continue
            cross = {frozenset((a, b)) for a in s_in for b in t_in}
            if cross & expected_group_pairs(case):
                verdict = "correct"
                break
            if cross & separate_pairs(case):
                verdict = "wrong"
                break
        if verdict == "correct":
            correct += 1
        elif verdict == "wrong":
            wrong += 1
            details.append(f"误并：{source_id} → {target_id}")
        else:
            unverifiable += 1
    judged = correct + wrong
    if judged == 0:
        print("归并记录与回放案例无可对照的议题对（案例文章未落到被归并议题上）", file=sys.stderr)
        return 1
    precision = correct / judged
    ok = precision >= TARGET_MERGE_PRECISION
    print(
        f"归并建议精确率：{correct}/{judged} = {precision:.3f}（目标 ≥{TARGET_MERGE_PRECISION}，"
        f"另有 {unverifiable} 条归并无法对照案例验证）→ {'PASS' if ok else 'FAIL'}"
    )
    for d in details:
        print(f"  - {d}")
    return 0 if ok else 2


# ---------------------------------------------------------------------------
# 4. 跨语言归并率
# ---------------------------------------------------------------------------
def cmd_crosslingual(args: argparse.Namespace) -> int:
    from sqlalchemy import text

    try:
        cases = load_replay_cases(args.cases_dir)
    except NoDataError as exc:
        print(f"[无数据] {exc}", file=sys.stderr)
        return 1

    # 实际归簇结果：优先 --assignments 回放产物 JSON（{case_id: {article_id: topic_id}}），
    # 否则查库 topic_articles（按 url 关联案例文章）
    assignments: dict[str, dict[str, str]] = {}
    if args.assignments:
        with open(args.assignments, encoding="utf-8") as f:
            assignments = json.load(f)
    else:
        engine = connect_db()
        url_index = case_url_index(cases)
        map_sql = """
            SELECT ta.topic_id, a.url
            FROM topic_articles ta JOIN articles a ON a.id = ta.article_id
            WHERE a.url = ANY(:urls)
        """
        with engine.connect() as conn:
            rows = conn.execute(text(map_sql), {"urls": list(url_index)}).all()
        for topic_id, url in rows:
            case, art = url_index[url]
            assignments.setdefault(case["case_id"], {})[art["article_id"]] = str(topic_id)

    total = merged = 0
    misses = []
    for case in cases:
        cid = case["case_id"]
        amap = assignments.get(cid, {})
        for pair in case.get("ground_truth", {}).get("cross_language_pairs", []):
            if len(pair) != 2:
                continue
            t1, t2 = amap.get(pair[0]), amap.get(pair[1])
            if t1 is None or t2 is None:
                continue  # 未入簇，不可评
            total += 1
            if t1 == t2:
                merged += 1
            else:
                misses.append(f"{cid}: {pair[0]}({t1}) 与 {pair[1]}({t2}) 未同议题")
    if total == 0:
        print("无可评估的跨语言报道对（案例文章未入库/未入簇，或未提供 --assignments 回放产物）", file=sys.stderr)
        return 1
    rate = merged / total
    ok = rate >= TARGET_CROSSLINGUAL
    print(f"跨语言归并率：{merged}/{total} = {rate:.3f}（目标 ≥{TARGET_CROSSLINGUAL}）→ {'PASS' if ok else 'FAIL'}")
    for m in misses:
        print(f"  - {m}")
    return 0 if ok else 2


# ---------------------------------------------------------------------------
# all：汇总报告
# ---------------------------------------------------------------------------
def cmd_all(args: argparse.Namespace) -> int:
    results: list[dict] = []

    def run_metric(name: str, target: str, fn) -> None:
        try:
            value, detail, passed = fn()
            results.append({"name": name, "target": target, "value": value,
                            "detail": detail, "status": "PASS" if passed else "FAIL"})
        except (NoDataError, EnvError) as exc:
            results.append({"name": name, "target": target, "value": None,
                            "detail": str(exc), "status": "NO_DATA"})

    def naming_metric():
        if not args.scores:
            raise NoDataError("未提供 --scores 人工打分文件（先 export-naming → 人工打分）")
        r = score_naming(args.scores)
        return f"{r['avg']}", f"{r['scored']}/{r['total_rows']} 条已评分，均分 {r['avg']}", r["pass"]

    def f1_metric():
        ns = argparse.Namespace(cases_dir=args.cases_dir, labels=args.labels, json_out=None)
        code, value = _capture_metric(cmd_topic_f1, ns)
        return value, f"宏平均 F1 = {value}", code == 0

    def merge_metric():
        ns = argparse.Namespace(cases_dir=args.cases_dir)
        code, value = _capture_metric(cmd_merge_precision, ns)
        return value, f"精确率 = {value}", code == 0

    def cross_metric():
        ns = argparse.Namespace(cases_dir=args.cases_dir, assignments=args.assignments)
        code, value = _capture_metric(cmd_crosslingual, ns)
        return value, f"归并率 = {value}", code == 0

    def _capture_metric(fn, ns):
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = fn(ns)
        out = buf.getvalue().strip()
        if code == 1:
            raise NoDataError(out.splitlines()[-1] if out else "无数据")
        # 从输出提取数值（"= x" 或 "均分 x"）
        value = "-"
        for line in out.splitlines():
            if "=" in line:
                value = line.split("=")[-1].strip().split("（")[0].strip()
                break
        return code, value

    run_metric("命名盲评均分", f"≥{TARGET_NAMING}", naming_metric)
    run_metric("主题分类宏平均 F1", f"≥{TARGET_F1}", f1_metric)
    run_metric("归并建议精确率", f"≥{TARGET_MERGE_PRECISION}", merge_metric)
    run_metric("跨语言同事件归并率", f"≥{TARGET_CROSSLINGUAL}", cross_metric)

    if all(r["status"] == "NO_DATA" for r in results):
        print("全部指标无留痕数据，无法出具评估结论", file=sys.stderr)
        return 1
    verdict = "PASS" if all(r["status"] == "PASS" for r in results) else "FAIL"

    lines = [
        "# M5 LLM 评估报告（T5.5）",
        "",
        f"- 生成时间：{datetime.now(UTC).isoformat()}",
        "- 数据来源：llm_judgements 留痕 + topics.revision_log 归并留痕 + 回放案例集"
        f"（{args.cases_dir}/replay_case_*.json）",
        "",
        "| 指标 | 目标 | 实测 | 结论 | 说明 |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['name']} | {r['target']} | {r['value'] or '无数据'} | {r['status']} | {r['detail']} |"
        )
    lines += ["", f"## 总结论：**{verdict}**", ""]
    fails = [r for r in results if r["status"] == "FAIL"]
    if fails:
        lines.append("不达标明细：")
        for r in fails:
            lines.append(f"- {r['name']}：{r['detail']}（目标 {r['target']}）")
        lines.append("")
    report = "\n".join(lines)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    print(f"\n报告已写入 {args.out}")
    return 0 if verdict == "PASS" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="T5.5 LLM 质量评估（基于 llm_judgements 留痕与回放案例集）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_exp = sub.add_parser("export-naming", help="导出命名盲评 CSV（需 DB）")
    p_exp.add_argument("--out", default="docs/dev/reviews/naming_blind_eval.csv")
    p_exp.add_argument("--limit", type=int, default=100)

    p_score = sub.add_parser("score-naming", help="统计人工打分（离线可用）")
    p_score.add_argument("--scores", required=True, help="export-naming 导出并经人工填写 score 列的 CSV")

    p_f1 = sub.add_parser("topic-f1", help="主题分类宏平均 F1")
    p_f1.add_argument("--cases-dir", default=DEFAULT_CASES_DIR)
    p_f1.add_argument("--labels", default=None, help="人工标注 CSV（topic_id,category），可选")
    p_f1.add_argument("--json-out", default=None)

    p_merge = sub.add_parser("merge-precision", help="归并建议精确率")
    p_merge.add_argument("--cases-dir", default=DEFAULT_CASES_DIR)

    p_cross = sub.add_parser("crosslingual", help="跨语言同事件归并率")
    p_cross.add_argument("--cases-dir", default=DEFAULT_CASES_DIR)
    p_cross.add_argument("--assignments", default=None,
                         help="回放产物 JSON {case_id: {article_id: topic_id}}，不提供则查库 topic_articles")

    p_all = sub.add_parser("all", help="汇总四项指标出报告")
    p_all.add_argument("--cases-dir", default=DEFAULT_CASES_DIR)
    p_all.add_argument("--scores", default=None)
    p_all.add_argument("--labels", default=None)
    p_all.add_argument("--assignments", default=None)
    p_all.add_argument("--out", default=DEFAULT_REPORT)

    args = parser.parse_args()
    try:
        if args.cmd == "export-naming":
            return cmd_export_naming(args)
        if args.cmd == "score-naming":
            return cmd_score_naming(args)
        if args.cmd == "topic-f1":
            return cmd_topic_f1(args)
        if args.cmd == "merge-precision":
            return cmd_merge_precision(args)
        if args.cmd == "crosslingual":
            return cmd_crosslingual(args)
        return cmd_all(args)
    except EnvError as exc:
        print(f"[环境不可达] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
