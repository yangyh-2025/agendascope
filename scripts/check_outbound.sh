#!/bin/bash
# AgendaScope 观澜 — 外发连接自检（Phase 5 T5.8）
#
# 目标：验证系统除"数据源拉取"外零外联。
#
# 模式：
#   --static    静态盘点：解析 deploy/docker-compose.yml 与环境变量中的外部端点
#               （RSSHub、GDELT、LLM API base 等），并扫描 backend/ 源码中硬编码的
#               http(s) 外链（白名单过滤：文档 URL、schema 命名空间、localhost、注释示例）。
#               任何环境可运行，仅输出清单，不改 exit code。
#   （默认）    运行态审计：需要 Linux 部署主机 + 运行中的 compose 栈。
#               Windows Git Bash 下无法做容器网络命名空间采样，给出提示并以 exit 0 优雅退出。
#
# 运行态审计实现原理：
#   1. 构建白名单 = 数据库 sources 表 feed_url/homepage_url 域名（docker compose exec db psql）
#      + GDELT 端点 + compose 配置中的外部 API 域名；
#   2. 白名单域名经 getent ahosts / dig 解析为 IP 集合；
#   3. 采样 AUDIT_SECONDS（默认 120）秒内各容器外发连接：
#      容器网络在独立 netns，宿主机 `ss -tunp` 看不到容器发起的 NAT 连接（只能看到
#      docker-proxy 的入站转发），因此采用逐容器 `docker exec <c> sh -c "ss -tun || netstat -tun"`
#      采样，这才是能真实看到容器外发连接的方式；
#   4. 汇总唯一 remote ip:port，排除私网/环回/链路本地/组播/DNS(53)/docker 内部网段；
#      命中白名单 IP → 数据源拉取，允许；其余 → 违规外联，逐条列出并 exit 1。
#
# 已知局限（报告会注明）：
#   - CDN/Anycast 域名 IP 动态变化，审计时解析结果可能与采样期实际连接 IP 不完全一致，
#     可能产生少量误报；违规清单请结合域名反查人工复核；
#   - 采样为周期性快照（每 3 秒一次），生命周期短于采样间隔的连接可能漏采，
#     可调大 AUDIT_SECONDS 提高覆盖率；
#   - UDP 53（DNS）被排除，无法审计 DNS 隧道类隐蔽外联。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/deploy/docker-compose.yml"
AUDIT_SECONDS="${AUDIT_SECONDS:-120}"
MODE="${1:-runtime}"

info() { echo "[信息] $*"; }
warn() { echo "[警告] $*"; }
die() { echo "[错误] $*" >&2; exit 1; }

# 内部服务名/本机地址：配置中出现但不是"外联"
is_internal_host() {
  case "$1" in
    ""|localhost|127.0.0.1|0.0.0.0|::1|db|redis|backend|worker|nlp-worker|cluster-worker|naming-worker|agenda-worker|snapshot-worker|elasticsearch|rsshub) return 0 ;;
    *) return 1 ;;
  esac
}

extract_host() {
  # 从 URL 中提取主机名
  sed -E 's#^[a-zA-Z][a-zA-Z0-9+.-]*://([^@/:]+@)?([^/:]+).*#\2#' <<<"$1"
}

# ============================================================
# --static：静态盘点
# ============================================================
if [ "${MODE}" = "--static" ]; then
  echo "========================================"
  echo " 外发连接自检 — 静态盘点"
  echo "========================================"

  echo ""
  echo "== 1. compose 配置中的外部端点 =="
  if docker compose version >/dev/null 2>&1 || command -v docker-compose >/dev/null 2>&1; then
    if docker compose version >/dev/null 2>&1; then COMPOSE=(docker compose); else COMPOSE=(docker-compose); fi
    CONFIG_TEXT="$("${COMPOSE[@]}" -f "${COMPOSE_FILE}" config 2>/dev/null || true)"
    if [ -z "${CONFIG_TEXT}" ]; then
      warn "compose config 无输出（Docker 守护进程未运行？），本节跳过"
    else
      grep -oE 'https?://[^ "'"'"']+' <<<"${CONFIG_TEXT}" | sort -u | while IFS= read -r url; do
          host="$(extract_host "${url}")"
          if is_internal_host "${host}"; then
            echo "  [内部] ${url}"
          else
            echo "  [外部] ${url}"
          fi
        done
    fi
  else
    warn "无 docker compose，改为直接扫描 ${COMPOSE_FILE} 文本"
    grep -oE 'https?://[^ "'"'"']+' "${COMPOSE_FILE}" | sort -u | sed 's/^/  /'
  fi

  echo ""
  echo "== 2. 源码默认配置端点（backend/app/config.py）=="
  grep -nE '^\s+[a-z_]*(api_base|_url|_base|proxy)[a-z_]*:\s*str\s*=\s*"https?://' "${REPO_ROOT}/backend/app/config.py" \
    | sed 's/^/  /' || echo "  （无）"

  echo ""
  echo "== 3. backend/ 源码硬编码 http(s) 外链（已过滤文档/schema/localhost 白名单）=="
  # 白名单：W3C schema 命名空间、文档/示例域名、本机地址、pypi 等打包元数据
  WHITELIST_RE='(localhost|127\.0\.0\.1|0\.0\.0\.0|example\.(com|org|net)|w3\.org|json-schema\.org|schema\.org|schemas\.|pypi\.org|python\.org|github\.com|gnu\.org|apache\.org|opensource\.org|agendascope\.local)'
  HITS=$(grep -rnoE 'https?://[A-Za-z0-9._~:/?#@!$&()*+,;=%-]+' "${REPO_ROOT}/backend/app" --include='*.py' \
    | grep -vE "${WHITELIST_RE}" || true)
  if [ -n "${HITS}" ]; then
    echo "${HITS}" | sed 's/^/  /'
    HIT_COUNT=$(echo "${HITS}" | wc -l)
    echo ""
    echo "  以上 ${HIT_COUNT} 处硬编码外链需人工确认是否为数据源端点或文档注释"
  else
    echo "  （未发现白名单外的硬编码外链）"
  fi

  echo ""
  echo "静态盘点完成。运行态审计请在 Linux 部署主机上执行: bash scripts/check_outbound.sh"
  exit 0
fi

# ============================================================
# 运行态审计（默认）
# ============================================================
echo "========================================"
echo " 外发连接自检 — 运行态审计"
echo "========================================"

# Git Bash / 非 Linux：容器 netns 采样不可用，优雅退出
if [ "$(uname -s)" != "Linux" ]; then
  warn "当前环境（$(uname -s)）不支持容器网络命名空间采样，运行态审计仅限 Linux 部署主机。"
  echo "  可先执行静态盘点: bash scripts/check_outbound.sh --static"
  exit 0
fi

command -v docker >/dev/null 2>&1 || die "未检测到 Docker"
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  die "未检测到 docker compose"
fi
[ -f "${COMPOSE_FILE}" ] || die "未找到 ${COMPOSE_FILE}"

mapfile -t CONTAINERS < <("${COMPOSE[@]}" -f "${COMPOSE_FILE}" ps -q 2>/dev/null)
[ "${#CONTAINERS[@]}" -gt 0 ] || die "compose 栈未运行，请先启动服务（compose up -d）"
info "审计容器数: ${#CONTAINERS[@]}，采样时长: ${AUDIT_SECONDS}s"

# ---------- Step 1: 构建白名单域名 ----------
declare -A WL_DOMAINS=()

# 1a. 数据库 sources 表（数据源拉取白名单的核心来源）
info "从数据库 sources 表提取数据源域名..."
SOURCE_URLS=$("${COMPOSE[@]}" -f "${COMPOSE_FILE}" exec -T db \
  psql -U agenda -d agendascope -tA \
  -c "SELECT feed_url FROM sources WHERE feed_url IS NOT NULL UNION SELECT homepage_url FROM sources WHERE homepage_url IS NOT NULL" \
  2>/dev/null) || warn "sources 表查询失败（数据库未就绪？），白名单仅含配置端点，误报风险升高"
if [ -n "${SOURCE_URLS:-}" ]; then
  while IFS= read -r url; do
    host="$(extract_host "${url}")"
    is_internal_host "${host}" || WL_DOMAINS["${host}"]=1
  done <<< "${SOURCE_URLS}"
fi

# 1b. GDELT 端点 + compose 配置中的外部 API 域名
GDELT_HOST="$(extract_host "${GDELT_API_BASE:-https://api.gdeltproject.org/api/v2/doc/doc}")"
WL_DOMAINS["${GDELT_HOST}"]=1
while IFS= read -r url; do
  host="$(extract_host "${url}")"
  is_internal_host "${host}" || WL_DOMAINS["${host}"]=1
done < <("${COMPOSE[@]}" -f "${COMPOSE_FILE}" config 2>/dev/null | grep -oE 'https?://[^ "'"'"']+' | sort -u)

info "白名单域名: ${#WL_DOMAINS[@]} 个"

# ---------- Step 2: 白名单域名 → IP 集合 ----------
declare -A WL_IPS=()
UNRESOLVED=()
for domain in "${!WL_DOMAINS[@]}"; do
  ips=""
  if command -v getent >/dev/null 2>&1; then
    ips=$(getent ahosts "${domain}" 2>/dev/null | awk '{print $1}' | sort -u)
  fi
  if [ -z "${ips}" ] && command -v dig >/dev/null 2>&1; then
    ips=$(dig +short A "${domain}" 2>/dev/null | grep -E '^[0-9.]+$')
  fi
  if [ -n "${ips}" ]; then
    while IFS= read -r ip; do WL_IPS["${ip}"]=1; done <<< "${ips}"
  else
    UNRESOLVED+=("${domain}")
  fi
done
info "白名单解析出 ${#WL_IPS[@]} 个 IP"
if [ "${#UNRESOLVED[@]}" -gt 0 ]; then
  warn "以下域名解析失败（审计期间其连接将被计为违规，请人工复核）: ${UNRESOLVED[*]}"
fi

# ---------- Step 3: 采样容器外发连接 ----------
SAMPLE_FILE="$(mktemp)"
trap 'rm -f "${SAMPLE_FILE}"' EXIT

is_excluded_ip() {
  # 私网/环回/链路本地/组播/docker 内部网段（172.16.0.0/12 已含在私网内）
  case "$1" in
    10.*|192.168.*|127.*|169.254.*|224.*|239.*|0.*|::1|fe80:*|ff*) return 0 ;;
    172.*)
      second="${1#172.}"; second="${second%%.*}"
      [ "${second}" -ge 16 ] && [ "${second}" -le 31 ] && return 0
      return 1 ;;
    *) return 1 ;;
  esac
}

info "开始采样（每 3 秒一次快照，共 ${AUDIT_SECONDS} 秒）..."
END_TS=$(( $(date +%s) + AUDIT_SECONDS ))
while [ "$(date +%s)" -lt "${END_TS}" ]; do
  for cid in "${CONTAINERS[@]}"; do
    # ss 优先，netstat 兜底（不同基础镜像可用工具不同）
    docker exec "${cid}" sh -c 'ss -tun 2>/dev/null || netstat -tun 2>/dev/null' 2>/dev/null \
      | awk 'NR>2 && ($1=="tcp" || $1=="udp") {print $5}' \
      | grep -E '^[0-9a-fA-F.:]+:[0-9]+$' >> "${SAMPLE_FILE}" || true
  done
  sleep 3
done

# ---------- Step 4: 汇总分类 ----------
ALLOWED_FILE="$(mktemp)"; VIOLATION_FILE="$(mktemp)"
trap 'rm -f "${SAMPLE_FILE}" "${ALLOWED_FILE}" "${VIOLATION_FILE}"' EXIT

TOTAL=0
while IFS= read -r endpoint; do
  ip="${endpoint%:*}"; port="${endpoint##*:}"
  [ "${port}" = "53" ] && continue                      # DNS 排除（见头部局限说明）
  is_excluded_ip "${ip}" && continue
  TOTAL=$((TOTAL + 1))
  if [ -n "${WL_IPS[${ip}]:-}" ]; then
    echo "${endpoint}" >> "${ALLOWED_FILE}"
  else
    echo "${endpoint}" >> "${VIOLATION_FILE}"
  fi
done < <(sort -u "${SAMPLE_FILE}")

ALLOWED_COUNT=0; [ -s "${ALLOWED_FILE}" ] && ALLOWED_COUNT=$(wc -l < "${ALLOWED_FILE}")
VIOLATION_COUNT=0; [ -s "${VIOLATION_FILE}" ] && VIOLATION_COUNT=$(wc -l < "${VIOLATION_FILE}")

echo ""
echo "========================================"
echo " 外发连接审计报告"
echo "========================================"
echo "  采样时长:        ${AUDIT_SECONDS}s"
echo "  审计容器数:      ${#CONTAINERS[@]}"
echo "  外联端点总数:    ${TOTAL}（已排除私网/环回/组播/DNS）"
echo "  白名单命中:      ${ALLOWED_COUNT}（数据源拉取，允许）"
echo "  违规外联:        ${VIOLATION_COUNT}"
echo ""
if [ "${ALLOWED_COUNT}" -gt 0 ]; then
  echo "  [允许] 数据源拉取连接:"
  sort -u "${ALLOWED_FILE}" | sed 's/^/    /'
  echo ""
fi
echo "  局限说明: CDN/Anycast 域名 IP 动态变化，白名单解析结果可能与采样期实际连接 IP"
echo "            不完全一致；采样为 3 秒周期快照，短生命周期连接可能漏采（可调大"
echo "            AUDIT_SECONDS）；UDP 53 已排除，无法审计 DNS 隧道类外联。"
echo ""

if [ "${VIOLATION_COUNT}" -gt 0 ]; then
  echo "  [违规] 非白名单外联明细:"
  sort -u "${VIOLATION_FILE}" | sed 's/^/    /'
  echo ""
  echo "审计结果: 不通过（存在违规外联）"
  exit 1
fi
echo "审计结果: 通过（除数据源拉取外零外联）"
