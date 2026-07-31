"""AgendaScope 观澜 — 一键运行/测试入口。

用法（在仓库根目录）：
    python run.py up [--build] [--no-frontend] [--follow-log backend]
        一键启动全栈：环境预检 → Docker 容器（db/redis/es/backend/全部 worker）
        → 可选前端 Vite dev server → 健康检查 → 打印访问 URL/默认账户 → 跟踪日志
    python run.py down             停止并移除容器
    python run.py status           查看容器状态
    python run.py logs [服务名]    查看日志（默认 backend，-f 跟踪，-n 行数）
    python run.py seed             导入种子源与初始管理员（幂等）
    python run.py frontend         仅启动前端开发服务器（Vite，Ctrl+C 退出）
    python run.py test [目标]      跑测试：unit / integration / assessment / frontend / all（默认 all）
    python run.py replay           跑议程识别回放测试（24 案例，独立库 agendascope_replay）
    python run.py doctor           环境预检：Docker/端口/venv/node_modules/模型权重

依赖：Docker Desktop 已启动；.venv 已安装后端依赖；frontend/node_modules 已安装。
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

# Windows 中文终端默认 GBK：强制 stdout/stderr 用 UTF-8 输出，避免中文乱码
# 与 UnicodeEncodeError（如 '✓' 无法编码进 gbk）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass  # 非文本流（如管道重定向到文件）时跳过

ROOT = Path(__file__).resolve().parent
DEPLOY = ROOT / "deploy"
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
MODELS = ROOT / "models"
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
ALL_SERVICES = CORE_SERVICES + WORKER_SERVICES

# 服务名 → 对外端口（供预检占用检测与健康提示）
SERVICE_PORTS = {
    "db": 5432,
    "redis": 6379,
    "elasticsearch": 9200,
    "backend": 8000,
    "rsshub": 1200,
}
# 容器名 → 期望健康状态（backend 由 wait_healthy 轮询判定）
HEALTHY_CHECK_SERVICES = ["db", "redis", "elasticsearch", "backend"]

CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"
RESET = "\033[0m"

# 状态标记：统一 ASCII，规避 Windows GBK 终端 UnicodeEncodeError
OK = "[OK]"
BAD = "[X]"
WARN = "[!]"


def _c(text: str, color: str = CYAN) -> str:
    # Windows 终端不支持 ANSI 时降级为纯文本
    if os.name == "nt" and os.environ.get("TERM") not in ("xterm", "xterm-256color"):
        return text
    return f"{color}{text}{RESET}"


def banner(text: str) -> None:
    print(f"\n{_c('=' * 66)}")
    print(_c(f"  {text}"))
    print(_c("=" * 66))


def run(cmd: list[str], cwd: Path = ROOT, env: dict | None = None, check: bool = True) -> int:
    print(f"$ {_c(' '.join(str(c) for c in cmd), DIM)}")
    merged_env = {**os.environ, **(env or {})}
    proc = subprocess.run(cmd, cwd=cwd, env=merged_env)
    if check and proc.returncode != 0:
        sys.exit(proc.returncode)
    return proc.returncode


def compose(*args: str, check: bool = True) -> int:
    return run(["docker", "compose", *args], cwd=DEPLOY, check=check)


# ---------------------------------------------------------------------------
# 环境预检（doctor）
# ---------------------------------------------------------------------------
def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _docker_ready() -> tuple[bool, str]:
    if shutil.which("docker") is None:
        return False, "未找到 docker 命令，请安装 Docker Desktop 并启动"
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=10, check=True)
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "docker info 超时——Docker Desktop 可能未启动，请先启动 Docker Desktop"
    except subprocess.CalledProcessError:
        return False, "docker info 失败——请确认 Docker Desktop 已启动并处于运行状态"


def _is_our_container_running() -> bool:
    """是否已有本项目的 docker compose 容器在运行（端口占用可能来自本项目）。"""
    try:
        out = subprocess.run(
            ["docker", "compose", "ps", "--format", "{{.Name}}"],
            cwd=DEPLOY, capture_output=True, text=True, timeout=10,
        ).stdout
        return bool(out.strip())
    except (subprocess.SubprocessError, OSError):
        return False


def _check_deps(quiet: bool = False) -> bool:
    """检查 Docker/venv/node_modules/模型权重，缺失时打印修复命令。返回是否全部就绪。"""
    if not quiet:
        banner("环境预检 doctor")
    ok = True
    ours_running = _is_our_container_running()

    d_ok, d_msg = _docker_ready()
    if not d_ok:
        print(f"  {_c(BAD, RED)} Docker: {d_msg}")
        ok = False
    else:
        print(f"  {_c(OK, GREEN)} Docker: 已就绪")
        ver = subprocess.run(["docker", "--version"], capture_output=True, text=True).stdout.strip()
        if ver:
            print(f"    {DIM}{ver}{RESET}")

    if not VENV_PY.exists():
        print(f"  {_c(BAD, RED)} Python 虚拟环境: 缺失 {VENV_PY}")
        print(f"    修复:  python -m venv .venv")
        print(f"           .venv/Scripts/pip install -r backend/requirements.txt")
        ok = False
    else:
        print(f"  {_c(OK, GREEN)} Python 虚拟环境: {VENV_PY.name} 就绪")

    if not (FRONTEND / "node_modules").exists():
        print(f"  {_c(BAD, RED)} 前端依赖: frontend/node_modules 缺失")
        print(f"    修复:  cd frontend && {NPM} install")
        ok = False
    else:
        print(f"  {_c(OK, GREEN)} 前端依赖: node_modules 就绪")

    for svc, port in SERVICE_PORTS.items():
        if _port_in_use(port):
            if ours_running:
                print(f"  {_c(OK, GREEN)} 端口 {port} ({svc}): 本项目容器已占用（正常）")
            else:
                print(f"  {_c(WARN, YELLOW)} 端口占用: {svc} 端口 {port} 已被其他进程占用")

    # 模型权重提示（不阻塞：允许 LLM 降级模式启动）
    llm_dir = MODELS / "Qwen2.5-0.5B-Instruct"
    emb_file = MODELS / "paraphrase-multilingual-mpnet-base-v2"
    if not (llm_dir.is_dir() or emb_file.is_dir()):
        print(f"  {_c(WARN, YELLOW)} 模型权重: models/ 下未发现 Qwen/嵌入模型")
        print(f"    LLM 将进入降级模式（c-TF-IDF 兜底），如需完整命名/分类/摘要请先准备模型权重")
    else:
        print(f"  {_c(OK, GREEN)} 模型权重: models/ 已就绪")

    if not ok:
        print(f"\n{_c('环境未就绪，请先按上方修复命令处理。', RED)}")
    elif not quiet:
        print(f"\n{_c('环境就绪，可执行 python run.py up。', GREEN)}")
    return ok


def cmd_doctor(_: argparse.Namespace) -> None:
    _check_deps()


# ---------------------------------------------------------------------------
# up / down / status / logs
# ---------------------------------------------------------------------------
def wait_healthy(service: str, timeout: int = 240) -> None:
    print(f"  等待 {_c(service)} 健康 ... ", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = subprocess.run(
            ["docker", "compose", "ps", "--format", "{{.Name}} {{.Status}}", service],
            cwd=DEPLOY, capture_output=True, text=True,
        ).stdout
        if "healthy" in out or ("Up" in out and "health" not in out):
            print(_c(" OK", GREEN))
            return
        print(".", end="", flush=True)
        time.sleep(3)
    print(f" {_c('超时（可继续观察 python run.py logs backend）', YELLOW)}")


def print_stack_info() -> None:
    """打印访问 URL、默认账户、后续操作指引。"""
    banner("服务已就绪")
    print(f"  后端 API:      {_c('http://localhost:8000')}")
    print(f"  API 文档:      {_c('http://localhost:8000/docs')}  (APP_DEBUG=true 时开放)")
    print(f"  前端 (Vite):   {_c('http://localhost:5173')}")
    print(f"  前端构建版:    {_c('http://localhost:8000')}   (后端托管构建产物)")
    print(f"  Redis 队列:    redis://localhost:6379/1   (Streams)")
    print(f"  Elasticsearch: http://localhost:9200")
    print()
    print(f"  {_c('首次使用：', GREEN)}")
    print(f"    1. 浏览器打开 {_c('http://localhost:5173')} 或 {_c('http://localhost:8000')}")
    print(f"    2. 访问 /setup 完成 5 步安装向导（或先执行 python run.py seed）")
    print(f"    3. 默认管理员: admin / Admin12345（首次登录强制改密）")
    print()
    print(f"  {_c('常用操作：', GREEN)}")
    print(f"    python run.py logs backend -f   跟踪后端日志")
    print(f"    python run.py seed               导入种子源与初始管理员（幂等）")
    print(f"    python run.py test               跑全部测试")
    print(f"    python run.py down               停止并移除容器")
    print(f"    python run.py status             查看容器状态")


def cmd_up(args: argparse.Namespace) -> None:
    banner("AgendaScope 观澜 — 一键启动")
    if not _check_deps(quiet=True):
        print(f"{_c('预检未通过，请先执行 python run.py doctor 查看修复命令。', RED)}")
        sys.exit(1)

    services = CORE_SERVICES + ([] if args.core else WORKER_SERVICES)
    print(f"\n{_c('启动服务:', CYAN)} {', '.join(services)}")
    cmd = ["up", "-d"] + (["--build"] if args.build else []) + services
    compose(*cmd)

    for svc in HEALTHY_CHECK_SERVICES:
        wait_healthy(svc)

    print_stack_info()

    # 一键启动前端（默认开，--no-frontend 关闭）
    if not args.no_frontend:
        print(f"\n{_c('启动前端 Vite dev server (Ctrl+C 停止)...', CYAN)}")
        try:
            run([NPM, "run", "dev"], cwd=FRONTEND, check=False)
        except KeyboardInterrupt:
            print(f"\n{_c('前端已停止。后端容器仍在运行：python run.py down 停止全部。', YELLOW)}")
    else:
        print(f"\n{_c('前端未启动（--no-frontend）。手动启动: python run.py frontend', DIM)}")


def cmd_down(_: argparse.Namespace) -> None:
    banner("停止全部服务")
    compose("down")


def cmd_status(_: argparse.Namespace) -> None:
    compose("ps")


def cmd_logs(args: argparse.Namespace) -> None:
    cmd = ["logs", "--tail", str(args.tail)] + (["-f"] if args.follow else []) + [args.service]
    compose(*cmd, check=False)


# ---------------------------------------------------------------------------
# seed / frontend / test / replay
# ---------------------------------------------------------------------------
def cmd_seed(_: argparse.Namespace) -> None:
    # 先确保数据库结构迁移到最新（backend 容器若是旧镜像，库内可能缺新表）
    run([str(VENV_PY), "-m", "alembic", "upgrade", "head"], cwd=BACKEND)
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

    p = sub.add_parser("up", help="一键启动全栈（容器+前端+日志指引）")
    p.add_argument("--build", action="store_true", help="先重建镜像")
    p.add_argument("--core", action="store_true", help="只启动 db/redis/es/backend，不启动 worker")
    p.add_argument("--no-frontend", action="store_true", help="不自动启动前端 Vite dev server")
    p.set_defaults(fn=cmd_up)

    sub.add_parser("down", help="停止并移除容器").set_defaults(fn=cmd_down)
    sub.add_parser("status", help="查看容器状态").set_defaults(fn=cmd_status)

    p = sub.add_parser("logs", help="查看服务日志")
    p.add_argument("service", nargs="?", default="backend")
    p.add_argument("-f", "--follow", action="store_true")
    p.add_argument("-n", "--tail", type=int, default=200, help="显示最近 N 行（默认 200）")
    p.set_defaults(fn=cmd_logs)

    sub.add_parser("doctor", help="环境预检：Docker/端口/venv/node_modules/模型").set_defaults(fn=cmd_doctor)
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
