"""T5.4 全链路延迟压测（AgendaScope 观澜 Phase 5）。

口径（docs/dev/4-开发计划.md T5.4 / 2.2 回放指标表）：
  - 按 ~2.2 万篇/天（≈15 篇/分钟）回放加压，峰值 100 篇/分钟周期性突发；
  - 端到端「新闻发布→平台可见」P95 ≤30 min 为 PASS，红线 ≤2 h；
  - 队列积压、LLM 耗时、快照刷新情况全量记录。

真实入口（读代码确认）：
  - 注入走内部采集 API：POST {COLLECT_API_BASE}/internal/collect
    （backend/app/api/routes/internal.py），Bearer 认证 COLLECTOR_INTERNAL_TOKEN；
    载荷为 app.schemas.collect.CollectedPayload（uuid/source_id/url/title/content/
    informant/pub_time/time_source 等）。CollectService.ingest 落 articles 并
    XADD Redis Stream「raw:articles」（app.db.queue.STREAM_RAW_ARTICLES），
    后续向量化/归簇由消费该 Stream 的 worker 异步完成。
  - 延迟样本表 pipeline_latency_sample（backend/app/nlp/latency.py）：
    article_id 唯一，字段 published_at/visible_at/latency_ms/latency_bucket/
    channel/country_code/sampled_at；分桶 <5m/5-15m/15-30m/30-60m/1-2h/>2h。
    注意：该表只覆盖 published_at→visible_at 端到端延迟，无逐环节分段字段；
    分段瓶颈用 Redis 队列积压 + llm_judgements.latency_ms + 快照落表时间佐证。

退出码：PASS=0 / FAIL=2 / 环境不可达=1（显式报错，不输出任何假数据）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta

# 与 tests/conftest.py 一致：sys.path 插入 backend 使可 import app 包
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(ROOT_DIR, "backend")
sys.path.insert(0, BACKEND_DIR)

DEFAULT_DATABASE_URL = "postgresql+psycopg2://agenda:agenda_dev_pwd@localhost:5432/agendascope"
DEFAULT_REDIS_URL = "redis://localhost:6379/1"  # redis_stream_url 默认 db1（app.config.Settings）
DEFAULT_API_BASE = "http://localhost:8000"

STREAM_RAW_ARTICLES = "raw:articles"  # app.db.queue.STREAM_RAW_ARTICLES

P95_TARGET_MIN = 30.0
REDLINE_MIN = 120.0

SOURCE_NAME = "STRESS-TEST 合成源"


class EnvError(RuntimeError):
    """环境不可达（DB/Redis/API），退出码 1。"""


# ---------------------------------------------------------------------------
# 环境检测与连接
# ---------------------------------------------------------------------------
def _database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def _redis_url() -> str:
    return os.environ.get("REDIS_STREAM_URL") or os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)


def connect_db():
    """返回 SQLAlchemy engine；不可达抛 EnvError。"""
    try:
        from sqlalchemy import create_engine, text
    except ImportError as exc:
        raise EnvError(f"缺少依赖 sqlalchemy（请用项目 .venv 运行）: {exc}") from exc
    url = _database_url()
    try:
        engine = create_engine(url, connect_args={"connect_timeout": 5})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        raise EnvError(f"PostgreSQL 不可达（DATABASE_URL={url}）: {exc}") from exc
    return engine


def connect_redis():
    """返回 redis 客户端；不可达抛 EnvError。"""
    try:
        import redis
    except ImportError as exc:
        raise EnvError(f"缺少依赖 redis（请用项目 .venv 运行）: {exc}") from exc
    url = _redis_url()
    try:
        client = redis.Redis.from_url(url, socket_connect_timeout=5)
        client.ping()
    except Exception as exc:
        raise EnvError(f"Redis 不可达（REDIS_STREAM_URL/REDIS_URL={url}）: {exc}") from exc
    return client


def check_api(api_base: str, token: str) -> None:
    """探测内部采集 API 可达且 token 有效（发一个空载荷期望 4xx 而非连接错误/401）。"""
    req = urllib.request.Request(
        api_base.rstrip("/") + "/internal/collect",
        data=b"{}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise EnvError(
                f"采集 API 可达但内部 token 非法（{api_base}，"
                "请设置 COLLECTOR_INTERNAL_TOKEN 与服务端一致）"
            ) from exc
        return  # 422 等参数错误说明 API 与鉴权正常
    except Exception as exc:
        raise EnvError(f"采集 API 不可达（{api_base}/internal/collect）: {exc}") from exc


# ---------------------------------------------------------------------------
# 合成源与注入
# ---------------------------------------------------------------------------
def ensure_stress_source(engine) -> uuid.UUID:
    """确保压测合成源存在，返回 source_id（幂等，按 name 查找）。"""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.models.source import Source

    with Session(engine) as db:
        source = db.scalars(select(Source).where(Source.name == SOURCE_NAME)).first()
        if source is None:
            source = Source(
                name=SOURCE_NAME,
                country_code="US",
                homepage_url="https://stress.example.com",
                feed_url="https://stress.example.com/feed.xml",
                collect_mode="rss",
                adapter_type="rss",
                media_type="online",
                language="en",
                poll_interval_min=5,
                audience_weight=10.0,
            )
            db.add(source)
            db.commit()
        return source.id


def inject_article(api_base: str, token: str, source_id: uuid.UUID, marker: str, seq: int) -> bool:
    """通过真实内部采集 API 注入一篇合成文章；url/title 带 marker 便于关联延迟样本。"""
    payload = {
        "uuid": str(uuid.uuid4()),
        "source_id": str(source_id),
        "adapter_type": "rss",
        "url": f"https://stress.example.com/{marker}/{seq}",
        "title": f"[{marker}] synthetic stress article {seq}",
        "content": f"Synthetic stress-test article {seq} for run {marker}. " * 3,
        "informant": SOURCE_NAME,
        "authors": [],
        "pub_time": datetime.now(UTC).isoformat(),
        "content_status": "full",
        "time_source": "feed",
    }
    req = urllib.request.Request(
        api_base.rstrip("/") + "/internal/collect",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return not body.get("data", {}).get("duplicate", False)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        print(f"[inject] HTTP {exc.code} seq={seq}: {detail}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"[inject] 请求失败 seq={seq}: {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# 采样统计（run / report 共用）
# ---------------------------------------------------------------------------
def collect_latency_stats(engine, marker: str | None, since: datetime | None) -> dict:
    """从 pipeline_latency_sample 统计 P50/P95/P99/max 与分桶分布。

    marker 非空时按 articles.url 关联只统计本次压测注入的文章。
    """
    from sqlalchemy import text

    where = []
    params: dict = {}
    join = ""
    if marker:
        join = "JOIN articles a ON a.id = s.article_id"
        where.append("a.url LIKE :marker")
        params["marker"] = f"%{marker}%"
    if since is not None:
        where.append("s.sampled_at >= :since")
        params["since"] = since
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    stats_sql = f"""
        SELECT count(*) AS n,
               percentile_cont(0.5)  WITHIN GROUP (ORDER BY s.latency_ms) / 60000.0 AS p50_min,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY s.latency_ms) / 60000.0 AS p95_min,
               percentile_cont(0.99) WITHIN GROUP (ORDER BY s.latency_ms) / 60000.0 AS p99_min,
               max(s.latency_ms) / 60000.0 AS max_min,
               count(*) FILTER (WHERE s.latency_ms > :redline_ms) AS over_redline
        FROM pipeline_latency_sample s {join} {where_sql}
    """
    params["redline_ms"] = int(REDLINE_MIN * 60000)
    bucket_sql = f"""
        SELECT s.latency_bucket, count(*) AS n
        FROM pipeline_latency_sample s {join} {where_sql}
        GROUP BY s.latency_bucket
    """
    channel_sql = f"""
        SELECT s.channel,
               count(*) AS n,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY s.latency_ms) / 60000.0 AS p95_min
        FROM pipeline_latency_sample s {join} {where_sql}
        GROUP BY s.channel ORDER BY s.channel
    """
    with engine.connect() as conn:
        row = conn.execute(text(stats_sql), params).mappings().one()
        buckets = {r.latency_bucket: int(r.n) for r in conn.execute(text(bucket_sql), params)}
        channels = [
            {"channel": r.channel, "n": int(r.n), "p95_min": round(float(r.p95_min), 2)}
            for r in conn.execute(text(channel_sql), params)
        ]
    return {
        "n": int(row["n"]),
        "p50_min": None if row["p50_min"] is None else round(float(row["p50_min"]), 2),
        "p95_min": None if row["p95_min"] is None else round(float(row["p95_min"]), 2),
        "p99_min": None if row["p99_min"] is None else round(float(row["p99_min"]), 2),
        "max_min": None if row["max_min"] is None else round(float(row["max_min"]), 2),
        "over_redline": int(row["over_redline"]),
        "buckets": buckets,
        "channels": channels,
    }


def collect_llm_stats(engine, since: datetime) -> dict:
    """llm_judgements.latency_ms 在窗口内的统计（LLM 耗时佐证）。"""
    from sqlalchemy import text

    sql = """
        SELECT task_type, count(*) AS n,
               percentile_cont(0.5)  WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
               max(latency_ms) AS max_ms,
               count(*) FILTER (WHERE NOT success) AS failed
        FROM llm_judgements
        WHERE created_at >= :since AND latency_ms IS NOT NULL
        GROUP BY task_type ORDER BY task_type
    """
    with engine.connect() as conn:
        rows = [
            {
                "task_type": r.task_type,
                "n": int(r.n),
                "p50_ms": int(r.p50_ms),
                "p95_ms": int(r.p95_ms),
                "max_ms": int(r.max_ms),
                "failed": int(r.failed),
            }
            for r in conn.execute(text(sql), {"since": since})
        ]
    return {"by_task": rows}


def collect_snapshot_stats(engine, since: datetime) -> dict:
    """agenda_snapshots 窗口内落表情况（刷新耗时无埋点，只能给落表量与最新时间）。"""
    from sqlalchemy import text

    sql = """
        SELECT count(*) AS n, max(created_at) AS latest
        FROM agenda_snapshots WHERE created_at >= :since
    """
    with engine.connect() as conn:
        row = conn.execute(text(sql), {"since": since}).mappings().one()
    return {
        "n": int(row["n"]),
        "latest": row["latest"].isoformat() if row["latest"] else None,
        "note": "快照刷新耗时无独立埋点，仅可统计窗口内落表行数与最新落表时间",
    }


def queue_backlog(redis_client) -> dict:
    """raw:articles 队列积压（XLEN + 各消费组 pending）。"""
    backlog = {"stream": STREAM_RAW_ARTICLES, "xlen": int(redis_client.xlen(STREAM_RAW_ARTICLES))}
    try:
        groups = redis_client.xinfo_groups(STREAM_RAW_ARTICLES)
        backlog["groups"] = [
            {"name": g.get("name"), "pending": int(g.get("pending", 0)), "lag": g.get("lag")}
            for g in groups
        ]
    except Exception:
        backlog["groups"] = []
    return backlog


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
BUCKET_ORDER = ("<5m", "5-15m", "15-30m", "30-60m", "1-2h", ">2h")


def judge(stats: dict) -> tuple[str, list[str]]:
    """PASS/FAIL 判定与瓶颈明细。"""
    reasons: list[str] = []
    if stats["n"] == 0:
        return "FAIL", ["无延迟样本（pipeline_latency_sample 在统计窗口内为 0 行），无法判定"]
    if stats["p95_min"] is None or stats["p95_min"] > P95_TARGET_MIN:
        reasons.append(
            f"端到端 P95 = {stats['p95_min']} min > 目标 {P95_TARGET_MIN:.0f} min"
        )
    if stats["over_redline"] > 0:
        reasons.append(f"{stats['over_redline']} 篇样本超过 2h 红线")
    # 瓶颈线索：>30m 样本集中在哪个桶
    slow = sum(stats["buckets"].get(b, 0) for b in ("30-60m", "1-2h", ">2h"))
    if slow:
        reasons.append(f"30 min 以上样本 {slow} 篇（30-60m/1-2h/>2h 桶），需按延迟预算分解表逐段定位")
    return ("PASS" if not reasons else "FAIL"), reasons


def render_report(
    verdict: str | None,
    reasons: list[str],
    stats: dict,
    *,
    marker: str | None,
    since: datetime | None,
    backlog_series: list[dict] | None = None,
    llm_stats: dict | None = None,
    snapshot_stats: dict | None = None,
    run_meta: dict | None = None,
) -> str:
    lines = [
        "# M5 压测报告（T5.4 全链路延迟）",
        "",
        f"- 生成时间：{datetime.now(UTC).isoformat()}",
        "- 统计口径：pipeline_latency_sample（published_at→visible_at，端到端）",
        f"- 样本筛选：marker={marker or '（全部样本）'}；since={since.isoformat() if since else '（不限）'}",
        f"- 判定标准：P95 ≤ {P95_TARGET_MIN:.0f} min 为 PASS；红线 ≤ {REDLINE_MIN:.0f} min",
        "",
    ]
    if run_meta:
        lines += [
            "## 压测运行参数",
            "",
            f"- 注入入口：POST {run_meta['api_base']}/internal/collect（内部采集 API，Bearer token 认证）",
            f"- 常态速率：{run_meta['rate_per_min']} 篇/分钟；峰值：{run_meta['peak_per_min']} 篇/分钟"
            f"（每 {run_meta['peak_every_min']} 分钟突发一次）；持续 {run_meta['duration_min']} 分钟",
            f"- 实际注入：{run_meta['injected']} 篇（去重后接受 {run_meta['accepted']} 篇，失败 {run_meta['failed']} 篇）",
            "",
        ]
    lines += [
        "## 端到端延迟统计",
        "",
        f"- 样本量：{stats['n']}",
        f"- P50：{stats['p50_min']} min；P95：{stats['p95_min']} min；"
        f"P99：{stats['p99_min']} min；max：{stats['max_min']} min",
        f"- 超过 2h 红线样本：{stats['over_redline']} 篇",
        "",
        "| 分桶 | 样本数 |",
        "|---|---|",
    ]
    for b in BUCKET_ORDER:
        lines.append(f"| {b} | {stats['buckets'].get(b, 0)} |")
    lines += ["", "| 通道 | 样本数 | P95 (min) |", "|---|---|---|"]
    for c in stats["channels"]:
        lines.append(f"| {c['channel']} | {c['n']} | {c['p95_min']} |")
    lines.append("")
    if backlog_series:
        lines += ["## Redis 队列积压（raw:articles）", "", "| 时间 | XLEN | 各消费组 pending |", "|---|---|---|"]
        for snap in backlog_series:
            groups = ", ".join(
                f"{g['name']}={g['pending']}" for g in snap.get("groups", [])
            ) or "-"
            lines.append(f"| {snap['at']} | {snap['xlen']} | {groups} |")
        lines.append("")
    if llm_stats and llm_stats["by_task"]:
        lines += ["## LLM 耗时（llm_judgements.latency_ms）", "", "| task_type | 次数 | P50 (ms) | P95 (ms) | max (ms) | 失败 |", "|---|---|---|---|---|---|"]
        for r in llm_stats["by_task"]:
            lines.append(
                f"| {r['task_type']} | {r['n']} | {r['p50_ms']} | {r['p95_ms']} | {r['max_ms']} | {r['failed']} |"
            )
        lines.append("")
    if snapshot_stats:
        lines += [
            "## 快照刷新（agenda_snapshots）",
            "",
            f"- 窗口内落表行数：{snapshot_stats['n']}；最新落表：{snapshot_stats['latest']}",
            f"- 说明：{snapshot_stats['note']}",
            "",
        ]
    if verdict:
        lines += ["## 结论", "", f"**{verdict}**", ""]
        for r in reasons:
            lines.append(f"- {r}")
        lines.append("")
    return "\n".join(lines)


def write_report(out_path: str, content: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"报告已写入 {out_path}")


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------
def cmd_report(args: argparse.Namespace) -> int:
    try:
        engine = connect_db()
    except EnvError as exc:
        print(f"[环境不可达] {exc}", file=sys.stderr)
        return 1
    since = datetime.now(UTC) - timedelta(hours=args.since_hours) if args.since_hours else None
    stats = collect_latency_stats(engine, args.marker, since)
    verdict, reasons = judge(stats)
    report = render_report(verdict, reasons, stats, marker=args.marker, since=since)
    write_report(args.out, report)
    print(f"判定：{verdict}（样本 {stats['n']}，P95={stats['p95_min']} min）")
    for r in reasons:
        print(f"  - {r}")
    return 0 if verdict == "PASS" else 2


def cmd_run(args: argparse.Namespace) -> int:
    api_base = os.environ.get("COLLECT_API_BASE", DEFAULT_API_BASE)
    token = os.environ.get("COLLECTOR_INTERNAL_TOKEN", "")
    if not token:
        print("[环境不可达] 未设置 COLLECTOR_INTERNAL_TOKEN 环境变量", file=sys.stderr)
        return 1
    try:
        engine = connect_db()
        redis_client = connect_redis()
        check_api(api_base, token)
    except EnvError as exc:
        print(f"[环境不可达] {exc}", file=sys.stderr)
        return 1

    marker = args.marker or f"stress-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    source_id = ensure_stress_source(engine)
    run_start = datetime.now(UTC)
    print(f"压测开始 marker={marker} source_id={source_id}")
    print(f"速率 {args.rate_per_min}/min，峰值 {args.peak_per_min}/min 每 {args.peak_every_min} min，持续 {args.duration_min} min")

    injected = accepted = failed = 0
    backlog_series: list[dict] = []
    deadline = time.monotonic() + args.duration_min * 60
    minute_idx = 0
    while time.monotonic() < deadline:
        per_min = args.peak_per_min if (minute_idx % args.peak_every_min == args.peak_every_min - 1) else args.rate_per_min
        interval = 60.0 / per_min
        minute_end = min(time.monotonic() + 60.0, deadline)
        while time.monotonic() < minute_end:
            ok = inject_article(api_base, token, source_id, marker, injected)
            injected += 1
            if ok:
                accepted += 1
            else:
                failed += 1
            time.sleep(max(0.0, min(interval, minute_end - time.monotonic())))
        snap = queue_backlog(redis_client)
        snap["at"] = datetime.now(UTC).isoformat(timespec="seconds")
        backlog_series.append(snap)
        print(f"[{snap['at']}] 已注入 {injected}（接受 {accepted} / 失败 {failed}），队列 XLEN={snap['xlen']}")
        minute_idx += 1

    # 等管线消化残余队列后再采样
    print(f"注入完成，等待 {args.settle_min} min 让管线消化后采样……")
    settle_deadline = time.monotonic() + args.settle_min * 60
    while time.monotonic() < settle_deadline:
        snap = queue_backlog(redis_client)
        snap["at"] = datetime.now(UTC).isoformat(timespec="seconds")
        backlog_series.append(snap)
        if snap["xlen"] == 0 and all(g.get("pending", 0) == 0 for g in snap.get("groups", [])):
            break
        time.sleep(30)

    stats = collect_latency_stats(engine, marker, run_start)
    llm_stats = collect_llm_stats(engine, run_start)
    snapshot_stats = collect_snapshot_stats(engine, run_start)
    verdict, reasons = judge(stats)
    report = render_report(
        verdict, reasons, stats,
        marker=marker, since=run_start,
        backlog_series=backlog_series,
        llm_stats=llm_stats, snapshot_stats=snapshot_stats,
        run_meta={
            "api_base": api_base,
            "rate_per_min": args.rate_per_min,
            "peak_per_min": args.peak_per_min,
            "peak_every_min": args.peak_every_min,
            "duration_min": args.duration_min,
            "injected": injected, "accepted": accepted, "failed": failed,
        },
    )
    write_report(args.out, report)
    print(f"判定：{verdict}（样本 {stats['n']}，P95={stats['p95_min']} min，max={stats['max_min']} min）")
    for r in reasons:
        print(f"  - {r}")
    return 0 if verdict == "PASS" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="T5.4 全链路延迟压测（真实内部采集 API 注入）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="加压 + 采样 + 出报告")
    p_run.add_argument("--rate-per-min", type=int, default=15, help="常态注入速率（篇/分钟，~2.2 万篇/天）")
    p_run.add_argument("--peak-per-min", type=int, default=100, help="峰值注入速率（篇/分钟）")
    p_run.add_argument("--peak-every-min", type=int, default=5, help="每隔多少分钟突发一次峰值")
    p_run.add_argument("--duration-min", type=int, default=30, help="压测持续分钟数")
    p_run.add_argument("--settle-min", type=int, default=10, help="注入完成后等待管线消化的分钟数")
    p_run.add_argument("--marker", default=None, help="本次运行的文章标记（默认 stress-时间戳）")
    p_run.add_argument("--out", default="docs/dev/reviews/M5-压测报告.md")

    p_rep = sub.add_parser("report", help="仅基于已有 pipeline_latency_sample 数据出统计")
    p_rep.add_argument("--marker", default=None, help="只统计某次 run 注入的文章（url 含 marker）")
    p_rep.add_argument("--since-hours", type=float, default=None, help="只统计最近 N 小时采样的样本")
    p_rep.add_argument("--out", default="docs/dev/reviews/M5-压测报告.md")

    args = parser.parse_args()
    if args.cmd == "run":
        return cmd_run(args)
    return cmd_report(args)


if __name__ == "__main__":
    sys.exit(main())
