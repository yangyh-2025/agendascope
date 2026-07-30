"""AgendaScope 观澜 — 一键运行/测试入口。

用法（在仓库根目录）：
    python run.py up [--build]     启动全栈（db/redis/es/backend/全部 worker）
    python run.py down             停止并移除容器
    python run.py status           查看容器状态
    python run.py logs [服务名]    查看日志（默认 backend，-f 跟踪）
    python run.py seed             导入种子源与初始管理员（幂等）
    python run.py frontend         启动前端开发服务器（Vite，Ctrl+C 退出）
    python run.py test [目标]      跑测试：unit / integration / assessment / frontend / all（默认 all）
    python run.py replay           跑议程识别回放测试（24 案例，独立库 agendascope_replay）

依赖：Docker Desktop 已启动；.venv 已安装后端依赖；frontend/node_modules 已安装。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEPLOY = ROOT / "deploy"
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"  # Windows
if not VENV_PY.exists():
    VENV_PY = ROOT / ".venv" / "bin" / "python"  # Linux/macOS

REPLAY_DB_URL = "postgresql+psycopg2://agenda:agenda_dev_pwd@localhost:5432/agendascope_replay"
NPM = "npm.cmd" if os.name == "nt" else "npm"

CORE_SERVICES = ["db", "redis", "elasticsearch", "backend"]
WORKER_SERVICES = [
    "worker", "nlp-worker", "cluster-worker", "naming-worker",
    "agenda-worker", "snapshot-worker", "detection-worker", "alerting-worker",
]


def run(cmd: list[str], cwd: Path = ROOT, env: dict | None = None, check: bool = True) -> int:
    print(f"$ {' '.join(str(c) for c in cmd)}")
    merged_env = {**os.environ, **(env or {})}
    proc = subprocess.run(cmd, cwd=cwd, env=merged_env)
    if check and proc.returncode != 0:
        sys.exit(proc.returncode)
    return proc.returncode


def compose(*args: str, check: bool = True) -> int:
    return run(["docker", "compose", *args], cwd=DEPLOY, check=check)


def wait_healthy(service: str, timeout: int = 180) -> None:
    print(f"等待 {service} 健康 ...", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = subprocess.run(
            ["docker", "compose", "ps", "--format", "{{.Name}} {{.Status}}", service],
            cwd=DEPLOY, capture_output=True, text=True,
        ).stdout
        if "healthy" in out or ("Up" in out and "health" not in out):
            print(" OK")
            return
        print(".", end="", flush=True)
        time.sleep(3)
    print(" 超时（可继续观察 python run.py logs）")


def cmd_up(args: argparse.Namespace) -> None:
    services = CORE_SERVICES + ([] if args.core else WORKER_SERVICES)
    cmd = ["up", "-d"] + (["--build"] if args.build else []) + services
    compose(*cmd)
    for svc in ("db", "redis", "elasticsearch", "backend"):
        wait_healthy(svc)
    print("\n后端 API:  http://localhost:8000  (文档 http://localhost:8000/docs ，仅 APP_DEBUG=true 时开放)")
    print("前端开发:  python run.py frontend  (http://localhost:5173)")
    print("首次使用:  python run.py seed  导入种子源后访问 /setup 完成 5 步安装向导")


def cmd_down(_: argparse.Namespace) -> None:
    compose("down")


def cmd_status(_: argparse.Namespace) -> None:
    compose("ps")


def cmd_logs(args: argparse.Namespace) -> None:
    cmd = ["logs", "--tail", "200"] + (["-f"] if args.follow else []) + [args.service]
    compose(*cmd, check=False)


def cmd_seed(_: argparse.Namespace) -> None:
    run([str(VENV_PY), "scripts/seed_sources.py"])


def cmd_frontend(_: argparse.Namespace) -> None:
    run([NPM, "run", "dev"], cwd=FRONTEND)


def cmd_test(args: argparse.Namespace) -> None:
    target = args.target
    if target in ("unit", "all"):
        run([str(VENV_PY), "-m", "pytest", "tests/unit", "-q"])
    if target in ("integration", "all"):
        print("（需要 db/redis 容器运行中：python run.py up --core）")
        run([str(VENV_PY), "-m", "pytest", "tests/integration", "-q"])
    if target in ("assessment", "all"):
        run([str(VENV_PY), "-m", "pytest", "tests/assessment", "-q"])
    if target in ("frontend", "all"):
        run([NPM, "test"], cwd=FRONTEND)


def ensure_replay_db() -> None:
    # 建库（已存在则报错忽略）并迁移到 head
    subprocess.run(
        ["docker", "exec", "agendascope-db-1", "psql", "-U", "agenda", "-d", "postgres",
         "-c", "CREATE DATABASE agendascope_replay;"],
        capture_output=True,
    )
    subprocess.run(
        ["docker", "exec", "agendascope-db-1", "psql", "-U", "agenda", "-d", "agendascope_replay",
         "-c", "CREATE EXTENSION IF NOT EXISTS vector;"],
        capture_output=True,
    )
    run([str(VENV_PY), "-m", "alembic", "upgrade", "head"], cwd=BACKEND,
        env={"DATABASE_URL": REPLAY_DB_URL})


def cmd_replay(_: argparse.Namespace) -> None:
    ensure_replay_db()
    rc = run(
        [str(VENV_PY), "-m", "app.assessment.replay",
         "--case-dir", "../tests/assessment/replay_cases",
         "--report", "../docs/dev/reviews/M5-回放测试报告.md"],
        cwd=BACKEND,
        env={"DATABASE_URL": REPLAY_DB_URL, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )
    print("\n回放报告: docs/dev/reviews/M5-回放测试报告.md")
    sys.exit(rc)


def main() -> None:
    parser = argparse.ArgumentParser(description="AgendaScope 观澜一键运行/测试")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("up", help="启动全栈")
    p.add_argument("--build", action="store_true", help="先重建镜像")
    p.add_argument("--core", action="store_true", help="只启动 db/redis/es/backend，不启动 worker")
    p.set_defaults(fn=cmd_up)

    sub.add_parser("down", help="停止并移除容器").set_defaults(fn=cmd_down)
    sub.add_parser("status", help="查看容器状态").set_defaults(fn=cmd_status)

    p = sub.add_parser("logs", help="查看服务日志")
    p.add_argument("service", nargs="?", default="backend")
    p.add_argument("-f", "--follow", action="store_true")
    p.set_defaults(fn=cmd_logs)

    sub.add_parser("seed", help="导入种子源与初始管理员").set_defaults(fn=cmd_seed)
    sub.add_parser("frontend", help="启动前端开发服务器").set_defaults(fn=cmd_frontend)

    p = sub.add_parser("test", help="跑测试")
    p.add_argument("target", nargs="?", default="all",
                   choices=["unit", "integration", "assessment", "frontend", "all"])
    p.set_defaults(fn=cmd_test)

    sub.add_parser("replay", help="跑议程识别回放测试").set_defaults(fn=cmd_replay)

    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
