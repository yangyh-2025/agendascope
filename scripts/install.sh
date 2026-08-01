#!/bin/bash
# AgendaScope 观澜 — 一键安装脚本（Phase 5 T5.9）
#
# 兼容环境：Linux 部署主机 / Windows Git Bash（开发机自测）。
#   - free / nproc 在 Git Bash 不存在：内存检查用 `command -v free` 兜底，缺失时跳过并注明；
#     CPU 核数依次尝试 nproc / sysctl / getconf，全部缺失则显示 unknown。
#   - Git Bash 会把容器内路径（如 /app/scripts）误转为 Windows 路径，检测到 MINGW/MSYS/CYGWIN
#     时导出 MSYS_NO_PATHCONV=1，docker compose 参数原样传递。
#
# 安装模式：
#   INSTALL_MODE=local（默认）  脚本已在源码树（或离线安装包）内运行，直接使用所在仓库根目录。
#   INSTALL_MODE=git            从 ${INSTALL_REPO_URL} 克隆到 ${AGENDASCOPE_HOME}/src，
#                               可用 INSTALL_REF 指定分支/tag。两者都拿不到代码时显式报错退出。
#
# 离线模式：检测到离线镜像目录（含 *.tar + SHA256SUMS）时，先 sha256sum -c 校验（失败退出），
#   再 docker load 逐个加载，跳过一切外网拉取（不 build / 不 pull / 不访问外网）。
#   离线镜像目录按以下顺序探测：
#     1. ${AGENDASCOPE_HOME}/offline/images
#     2. ${REPO_ROOT}/images            （build_offline_package.sh 产出的安装包布局）
#     3. ${REPO_ROOT}/deploy/offline/images
#
# 管理员初始密码：安装时随机生成（不硬编码），经 SEED_ADMIN_PASSWORD 显式传给种子导入容器；
#   后端 ensure_admin 以 must_change_password=True 落库，首次登录强制改密，密码仅在安装结束时打印一次。
set -euo pipefail

AGENDASCOPE_HOME="${AGENDASCOPE_HOME:-$HOME/agendascope}"
INSTALL_MODE="${INSTALL_MODE:-local}"
INSTALL_REPO_URL="${INSTALL_REPO_URL:-}"
INSTALL_REF="${INSTALL_REF:-}"
SEED_ADMIN_USERNAME="${SEED_ADMIN_USERNAME:-admin}"

case "$(uname -s 2>/dev/null || echo unknown)" in
  MINGW*|MSYS*|CYGWIN*) export MSYS_NO_PATHCONV=1 ;;
esac

die() { echo "[错误] $*" >&2; exit 1; }
info() { echo "[信息] $*"; }
warn() { echo "[警告] $*"; }

echo "========================================"
echo " AgendaScope 观澜 — 一键安装部署"
echo "========================================"
echo ""
echo "安装位置: ${AGENDASCOPE_HOME}"
echo "安装模式: ${INSTALL_MODE}"
echo ""

# ---------- Step 0: 环境自检 ----------
command -v docker >/dev/null 2>&1 || die "未检测到 Docker，请先安装 Docker 20+"
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  die "未检测到 docker compose（docker compose 插件或 docker-compose）"
fi

if command -v free >/dev/null 2>&1; then
  TOTAL_MEM=$(free -m | awk '/^Mem:/{print $2}')
  MEM_DISPLAY="${TOTAL_MEM}MB"
  if [ "${TOTAL_MEM}" -lt 8000 ]; then
    warn "内存 ${TOTAL_MEM}MB < 8GB（推荐），聚类与 LLM 推理可能受限，仍可继续"
  fi
else
  MEM_DISPLAY="unknown（当前环境无 free 命令，跳过内存检查，请自行确认 ≥8GB）"
fi

if command -v nproc >/dev/null 2>&1; then
  CPU_COUNT=$(nproc)
elif command -v sysctl >/dev/null 2>&1; then
  CPU_COUNT=$(sysctl -n hw.ncpu 2>/dev/null || echo unknown)
elif command -v getconf >/dev/null 2>&1; then
  CPU_COUNT=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo unknown)
else
  CPU_COUNT="unknown"
fi
echo "  Docker: OK | compose: ${COMPOSE[*]} | CPU: ${CPU_COUNT} 核 | 内存: ${MEM_DISPLAY}"
echo ""

# ---------- Step 1: 获取代码 ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "${INSTALL_MODE}" = "git" ]; then
  [ -n "${INSTALL_REPO_URL}" ] || die "INSTALL_MODE=git 需要设置 INSTALL_REPO_URL"
  command -v git >/dev/null 2>&1 || die "INSTALL_MODE=git 需要 git 命令"
  REPO_ROOT="${AGENDASCOPE_HOME}/src"
  mkdir -p "${AGENDASCOPE_HOME}"
  if [ -d "${REPO_ROOT}/.git" ]; then
    info "已有克隆 ${REPO_ROOT}，更新代码..."
    git -C "${REPO_ROOT}" fetch --tags origin
    if [ -n "${INSTALL_REF}" ]; then
      git -C "${REPO_ROOT}" checkout "${INSTALL_REF}"
    else
      git -C "${REPO_ROOT}" pull --ff-only
    fi
  else
    info "克隆 ${INSTALL_REPO_URL} 到 ${REPO_ROOT} ..."
    if [ -n "${INSTALL_REF}" ]; then
      git clone --branch "${INSTALL_REF}" "${INSTALL_REPO_URL}" "${REPO_ROOT}"
    else
      git clone "${INSTALL_REPO_URL}" "${REPO_ROOT}"
    fi
  fi
else
  # local：脚本所在仓库根目录（scripts/ 的上一级）
  REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
fi

[ -f "${REPO_ROOT}/deploy/docker-compose.yml" ] || \
  die "未找到 ${REPO_ROOT}/deploy/docker-compose.yml —— 无法取得有效代码，请检查 INSTALL_MODE/INSTALL_REPO_URL 或将本脚本置于源码树 scripts/ 下运行"
[ -f "${REPO_ROOT}/scripts/seed_sources.py" ] || \
  die "未找到 ${REPO_ROOT}/scripts/seed_sources.py —— 代码树不完整，中止安装"

COMPOSE_FILE="${REPO_ROOT}/deploy/docker-compose.yml"
info "代码目录: ${REPO_ROOT}"

# ---------- Step 2: 生成随机管理员初始密码（不硬编码，不落盘） ----------
# 后端密码策略（T1.7）：至少 10 位且包含大小写字母与数字。
# 随机串后拼接固定后缀 "Ag1" 兜底保证三种字符类别齐全。
gen_admin_password() {
  local raw=""
  if command -v openssl >/dev/null 2>&1; then
    raw=$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 14)
  elif command -v python3 >/dev/null 2>&1; then
    raw=$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(14)))")
  elif command -v python >/dev/null 2>&1; then
    raw=$(python -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(14)))")
  else
    raw=$(tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 14)
  fi
  [ -n "${raw}" ] || die "随机密码生成失败（无 openssl/python//dev/urandom 可用）"
  printf '%sAg1' "${raw}"
}
SEED_ADMIN_PASSWORD="$(gen_admin_password)"
info "管理员初始密码已随机生成（安装结束时显示一次，请妥善保存）"

# ---------- Step 3: 镜像准备（离线优先，在线才允许 build/pull） ----------
OFFLINE_IMAGES_DIR=""
for candidate in \
  "${AGENDASCOPE_HOME}/offline/images" \
  "${REPO_ROOT}/images" \
  "${REPO_ROOT}/deploy/offline/images"; do
  if [ -d "${candidate}" ] && [ -f "${candidate}/SHA256SUMS" ] && ls "${candidate}"/*.tar >/dev/null 2>&1; then
    OFFLINE_IMAGES_DIR="${candidate}"
    break
  fi
done

if [ -n "${OFFLINE_IMAGES_DIR}" ]; then
  echo ""
  info "========== 离线模式 =========="
  info "检测到离线镜像包: ${OFFLINE_IMAGES_DIR}"
  info "校验镜像包完整性（sha256sum -c）..."
  (cd "${OFFLINE_IMAGES_DIR}" && sha256sum -c SHA256SUMS) || die "离线镜像包校验失败，文件可能损坏，请重新获取安装包"
  for tar_file in "${OFFLINE_IMAGES_DIR}"/*.tar; do
    info "加载镜像: $(basename "${tar_file}")"
    docker load -i "${tar_file}"
  done
  info "离线镜像加载完成，跳过一切外网拉取（不 build / 不 pull）"

  # 离线包内附带的模型目录：若代码树 models/ 缺少必需权重，从离线包同级 models/ 补齐
  OFFLINE_ROOT="$(cd "${OFFLINE_IMAGES_DIR}/.." && pwd)"
  if [ ! -f "${REPO_ROOT}/models/lid.176.bin" ] && [ -d "${OFFLINE_ROOT}/models" ]; then
    info "从离线包复制模型权重到 ${REPO_ROOT}/models ..."
    mkdir -p "${REPO_ROOT}/models"
    cp -a "${OFFLINE_ROOT}/models/." "${REPO_ROOT}/models/"
  fi
else
  echo ""
  info "========== 在线模式 =========="
  info "在线构建/拉取镜像（仅首次需要，约 3-10 分钟）..."
  "${COMPOSE[@]}" -f "${COMPOSE_FILE}" build || die "镜像构建失败"
  "${COMPOSE[@]}" -f "${COMPOSE_FILE}" pull || die "基础镜像拉取失败（db/redis/elasticsearch/rsshub）"
fi

# 模型权重检查（云 API 模式：仅 lid.176.bin 必需——语言识别无 API 替代；
# sentence-transformers/Qwen 仅在本地嵌入/LLM 模式需要，云 API 模式缺失是正常，给提示）
for required in models/lid.176.bin; do
  if [ ! -e "${REPO_ROOT}/${required}" ]; then
    warn "缺少模型文件 ${REPO_ROOT}/${required} —— NLP worker 语言识别将无法启动，请参考 docs/ 补齐 models/ 后重启"
  fi
done
for optional in models/sentence-transformers models/Qwen2.5-0.5B-Instruct; do
  if [ ! -e "${REPO_ROOT}/${optional}" ]; then
    info "未发现 ${REPO_ROOT}/${optional} —— 云 API 模式（LLM_PROFILE=api / NLP_EMBEDDING_PROFILE=api）下不需要本地权重，可忽略"
  fi
done

# ---------- Step 4: 启动全栈 ----------
echo ""
info "启动全栈服务..."
"${COMPOSE[@]}" -f "${COMPOSE_FILE}" up -d || die "服务启动失败，请用 ${COMPOSE[*]} -f ${COMPOSE_FILE} logs 排查"

# ---------- Step 5: 等待后端就绪 ----------
info "等待后端就绪（最多 90 秒）..."
BACKEND_READY=0
for i in $(seq 1 45); do
  if curl -sf http://localhost:8000/api/v1/health >/dev/null 2>&1; then
    BACKEND_READY=1
    echo "  后端就绪 ($((i * 2))s)"
    break
  fi
  sleep 2
done
[ "${BACKEND_READY}" = "1" ] || die "后端 90 秒内未就绪，请用 ${COMPOSE[*]} -f ${COMPOSE_FILE} logs backend 排查"

# ---------- Step 6: 种子源导入（真实执行，失败即中止） ----------
# 执行方式依据：deploy/Dockerfile.backend 的 build context 是 ../backend，镜像内只有 app/ 包，
# 不含 scripts/seed_sources.py，因此不能 `compose exec backend python -m scripts.seed_sources`。
# 这里用 `compose run` 启一个一次性容器，把宿主机 scripts/ 只读挂载进 /app/scripts，
# 并设 PYTHONPATH=/app 使脚本内 `from app...` 可导入（脚本内 sys.path 插入的 ../backend
# 在容器内不存在，无害）。--no-deps：栈已 up，避免重复拉起依赖。
# compose 不会把宿主机 SEED_ADMIN_PASSWORD 透传进容器（docker-compose.yml 未声明该变量），
# 故用 -e 显式传入，保证 ensure_admin 读取到的就是本次生成的随机密码。
echo ""
info "导入种子源（38 源 + GDELT 兜底源 + 系统规则 + 初始管理员）..."
SEED_MOUNT_SRC="${REPO_ROOT}/scripts"
case "$(uname -s 2>/dev/null || echo unknown)" in
  MINGW*|MSYS*|CYGWIN*) SEED_MOUNT_SRC="$(cd "${REPO_ROOT}/scripts" && pwd -W | tr '\\' '/')" ;;
esac
"${COMPOSE[@]}" -f "${COMPOSE_FILE}" run --rm --no-deps \
  -v "${SEED_MOUNT_SRC}:/app/scripts:ro" \
  -e PYTHONPATH=/app \
  -e SEED_ADMIN_USERNAME="${SEED_ADMIN_USERNAME}" \
  -e SEED_ADMIN_PASSWORD="${SEED_ADMIN_PASSWORD}" \
  backend python /app/scripts/seed_sources.py || die "种子源导入失败（已中止，未伪造成功）"

echo ""
echo "========================================"
echo "  安装完成！"
echo "  访问: http://localhost:8000"
echo "  管理面板: http://localhost:8000/system"
echo ""
echo "  初始管理员账号: ${SEED_ADMIN_USERNAME}"
echo "  初始管理员密码: ${SEED_ADMIN_PASSWORD}"
echo "  （随机生成，仅此一次显示；账号带 must_change_password 标记，"
echo "    首次登录将被强制修改密码）"
echo "========================================"
