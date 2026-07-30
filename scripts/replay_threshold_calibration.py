"""T5.3 阈值标定取证：24 个回放案例的同事件对/跨事件对/跨语言对相似度分布。

用真实 mpnet Embedder 对全部案例文章向量化，按 ground_truth 标注计算三类
文章对余弦相似度分布，并对候选归并阈值做扫描（ pair 级代理指标；质心级
最终效果以全量回放重跑为准）。输出可直接贴入 M5 报告的"阈值标定记录"。

用法：
  cd backend && ../.venv/Scripts/python ../scripts/replay_threshold_calibration.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "backend"))

from app.assessment.replay import load_replay_cases  # noqa: E402


def cosine(a, b):
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def quantiles(vals):
    vals = sorted(vals)
    n = len(vals)
    def q(p):
        return vals[min(int(p * n), n - 1)]
    return f"min={vals[0]:.3f} p25={q(0.25):.3f} median={q(0.5):.3f} p75={q(0.75):.3f} max={vals[-1]:.3f} (n={n})"


def main() -> None:
    case_dir = pathlib.Path(__file__).resolve().parent.parent / "tests" / "assessment" / "replay_cases"
    cases = load_replay_cases(case_dir)
    print(f"案例 {len(cases)} 个")

    from app.nlp.embedding import Embedder
    embedder = Embedder()

    same_event: list[float] = []
    separate: list[float] = []
    separate_detail: list[tuple[str, str, str, float]] = []
    cross_lang: list[float] = []
    per_case_min_same: list[tuple[str, float]] = []

    for case in cases:
        embs = {}
        for a in case.articles:
            embs[a.article_id] = embedder.embed_article(a.title, None, a.content)
        case_same = []
        for group in case.ground_truth.expected_article_groups:
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    s = cosine(embs[group[i]], embs[group[j]])
                    same_event.append(s)
                    case_same.append(s)
        for a, b in case.ground_truth.expected_separate_pairs:
            s = cosine(embs[a], embs[b])
            separate.append(s)
            separate_detail.append((case.case_id, a, b, s))
        for a, b in case.ground_truth.cross_language_pairs:
            cross_lang.append(cosine(embs[a], embs[b]))
        if case_same:
            per_case_min_same.append((case.case_id, min(case_same)))

    print("\n== 分布 ==")
    print(f"同事件对 : {quantiles(same_event)}")
    print(f"跨事件对 : {quantiles(separate)}")
    print(f"跨语言对 : {quantiles(cross_lang)}")

    print("\n== 各案例同事件对最小值（升序，决定归并可达下限） ==")
    for cid, v in sorted(per_case_min_same, key=lambda x: x[1])[:10]:
        print(f"  {cid}: {v:.3f}")

    print("\n== 跨事件对最高值 Top-5（决定误并安全上限） ==")
    for cid, a, b, v in sorted(separate_detail, key=lambda x: -x[3])[:5]:
        print(f"  {cid} {a}-{b}: {v:.3f}")

    print("\n== 阈值扫描（pair 级代理：≥t 即视为可并） ==")
    print(f"{'阈值':>6} {'同事件召回':>10} {'跨语言召回':>10} {'跨事件误并率':>12}")
    t = 0.50
    while t <= 0.9001:
        r_same = sum(1 for s in same_event if s >= t) / len(same_event)
        r_cross = sum(1 for s in cross_lang if s >= t) / len(cross_lang) if cross_lang else 0.0
        r_sep = sum(1 for s in separate if s >= t) / len(separate) if separate else 0.0
        print(f"{t:>6.3f} {r_same:>10.1%} {r_cross:>10.1%} {r_sep:>12.1%}")
        t += 0.025


if __name__ == "__main__":
    main()
