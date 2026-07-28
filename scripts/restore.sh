#!/usr/bin/env bash
# AgendaScope 观澜 — 恢复脚本（Phase 5 T5.11）
#
# 用法:
#   bash scripts/restore.sh <备份时间戳 YYYYMMDD-HHMMSS>
#   bash scripts/restore.sh <全量备份文件路径 .sql.gz[.enc]>
#
# 流程：停写 → PG（drop schema 重建后灌全量 → 按时间顺序应用其后全部增量 CSV）
#       → ES 快照恢复 → Redis RDB/AOF 恢复 → 校验（PG 行数/ES 文档数/后端健康）
#       → 恢复写入方 → 输出分段耗时与 RTO（目标 ≤30 min）对照。
#
# 环境变量：
#   BACKUP_DIR        备份目录（默认 $HOME/agendascope/backups）
#   BACKUP_KEY_FILE   解密密钥文件（恢复 .enc 产物时必填，与 backup.sh 同一把密钥）
#   ES_URL / ES_REPO_NAME / ES_REPO_PATH  与 backup.sh 相同约定（path.repo 部署要求见 backup.sh 头部注释）
#   ES_SNAPSHOT       指定恢复快照名；缺省用 snapshot_<时间戳>，不存在则用仓库内最新快照
#   ES_INDICES        恢复/校验的索引（默认 articles）
#   REDIS_BACKUP      指定 Redis 备份文件；缺省找与全量同时间戳、否则最新一份
#   PG_CONTAINER / REDIS_CONTAINER / ES_CONTAINER / COMPOSE_FILE  覆盖默认值
#
# 注意：
#   - 增量恢复顺序强制为 full → 增量按时间戳升序，COPY FROM 逐表灌入（无 ON CONFLICT，
#     依赖水位线保证行不重叠；对非本体系产出的数据重复执行会产生主键冲突而失败，属预期保护）。
#   - 健康检查实际路由为 http://localhost:8000/health（挂在根路径，不在 /api/v1 前缀下，
#     见 backend/app/main.py:23 与 backend/app/api/routes/health.py）。
#   - 目标运行环境为 Linux 备份主机；Windows Git Bash 下亦可执行（不使用 -t TTY）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "${SCRIPT_DIR}")"
COMPOSE_FILE="${COMPOSE_FILE:-${PROJECT_ROOT}/deploy/docker-compose.yml}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/agendascope/backups}"
ES_URL="${ES_URL:-http://localhost:9200}"
ES_REPO_NAME="${ES_REPO_NAME:-backup}"
ES_REPO_PATH="${ES_REPO_PATH:-/usr/share/elasticsearch/backups}"
ES_INDICES="${ES_INDICES:-articles}"
PG_CONTAINER="${PG_CONTAINER:-agendascope-db-1}"
REDIS_CONTAINER="${REDIS_CONTAINER:-agendascope-redis-1}"
ES_CONTAINER="${ES_CONTAINER:-agendascope-elasticsearch-1}"
RTO_TARGET_SECONDS=1800   # RTO 目标 ≤30 min

# 写入方服务（compose 服务名，容器名为 agendascope-<svc>-1）
WRITER_SERVICES=(backend worker nlp-worker cluster-worker naming-worker agenda-worker snapshot-worker)

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
die() { log "[错误] $*"; exit 1; }
now_s() { date +%s; }

psqlc() { docker exec -i "${PG_CONTAINER}" psql -U agenda -d agendascope "$@"; }

decrypt_cat() {
  # decrypt_cat <文件>：.enc 用 BACKUP_KEY_FILE 解密，其余原样输出
  local f="$1"
  case "${f}" in
    *.enc)
      [ -n "${BACKUP_KEY_FILE:-}" ] || die "恢复加密产物需要 BACKUP_KEY_FILE（与备份时同一把密钥）"
      [ -f "${BACKUP_KEY_FILE}" ] || die "BACKUP_KEY_FILE 指定的密钥文件不存在: ${BACKUP_KEY_FILE}"
      openssl enc -d -aes-256-cbc -pbkdf2 -pass file:"${BACKUP_KEY_FILE}" -in "${f}"
      ;;
    *) cat "${f}" ;;
  esac
}

if [ $# -lt 1 ]; then
  echo "用法:"
  echo "  bash scripts/restore.sh <备份时间戳 YYYYMMDD-HHMMSS>"
  echo "  bash scripts/restore.sh <全量备份文件路径 .sql.gz[.enc]>"
  echo "  RTO 目标 ≤30 min，实际取决于数据量"
  exit 1
fi

T_START=$(now_s)
declare -A SEG

# ---- 0. 定位备份产物 ------------------------------------------------------------
ARG="$1"
if [ -f "${ARG}" ]; then
  PG_FILE="${ARG}"
  TS=$(basename "${PG_FILE}" | sed -n 's/^agendascope-\([0-9]\{8\}-[0-9]\{6\}\)\.sql\.gz.*/\1/p')
  [ -n "${TS}" ] || log "  [警告] 文件名不含标准时间戳，跳过增量接续与同名快照匹配（ES/Redis 用最新产物）"
else
  TS="${ARG}"
  PG_FILE=""
  for cand in "${BACKUP_DIR}/agendascope-${TS}.sql.gz.enc" "${BACKUP_DIR}/agendascope-${TS}.sql.gz"; do
    [ -f "${cand}" ] && PG_FILE="${cand}" && break
  done
  [ -n "${PG_FILE}" ] || die "找不到时间戳 ${TS} 对应的全量备份（${BACKUP_DIR}/agendascope-${TS}.sql.gz[.enc]）"
fi
log "全量备份: ${PG_FILE}"

# 收集该全量之后的增量（文件名时间戳字典序即时间序）
INC_FILES=()
if [ -n "${TS:-}" ]; then
  for f in "${BACKUP_DIR}"/agendascope-inc-*.tar.gz "${BACKUP_DIR}"/agendascope-inc-*.tar.gz.enc; do
    [ -e "${f}" ] || continue
    its=$(basename "${f}" | sed -n 's/^agendascope-inc-\([0-9]\{8\}-[0-9]\{6\}\)\..*/\1/p')
    if [ -n "${its}" ] && [[ "${its}" > "${TS}" ]]; then
      INC_FILES+=("${f}")
    fi
  done
fi
log "待应用增量: ${#INC_FILES[@]} 个${INC_FILES:+（按时间顺序依次 COPY FROM）}"

# ---- 1. 停写（逐个确认） --------------------------------------------------------
T0=$(now_s)
for svc in "${WRITER_SERVICES[@]}"; do
  docker compose -f "${COMPOSE_FILE}" stop "${svc}" > /dev/null
  if docker ps --format '{{.Names}}' | grep -q "^agendascope-${svc}-1$"; then
    die "服务 ${svc} 停止失败（容器 agendascope-${svc}-1 仍在运行），中止恢复"
  fi
  log "  已停止: ${svc}"
done
SEG[停写]=$(($(now_s) - T0))

# ---- 2. PostgreSQL 恢复 ----------------------------------------------------------
T0=$(now_s)
docker exec "${PG_CONTAINER}" pg_isready -U agenda -d agendascope > /dev/null || die "PG 容器不可用"
# drop schema public cascade 重建（pg_dump 产物内含 CREATE EXTENSION vector 等对象，可完整重放；
# 比 dropdb/createdb 稳妥：不需要断开全部连接独占数据库）
psqlc -v ON_ERROR_STOP=1 -q -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
decrypt_cat "${PG_FILE}" | gunzip | psqlc -v ON_ERROR_STOP=1 -q
log "  全量灌入完成"

# 增量按时间顺序应用；COPY FROM 按表灌入，表顺序满足外键依赖
INC_TABLE_ORDER="articles topics topic_articles agenda_events agenda_event_evidence agenda_snapshots alerts alert_rules"
for inc in "${INC_FILES[@]+"${INC_FILES[@]}"}"; do
  [ -n "${inc}" ] || continue
  INC_TMP=$(mktemp -d)
  decrypt_cat "${inc}" | tar -xzf - -C "${INC_TMP}"
  for t in ${INC_TABLE_ORDER}; do
    if [ -f "${INC_TMP}/${t}.csv" ]; then
      psqlc -v ON_ERROR_STOP=1 -q -c "COPY ${t} FROM STDIN WITH (FORMAT csv, HEADER true)" < "${INC_TMP}/${t}.csv"
      log "  增量应用 ${t} <- $(basename "${inc}")"
    fi
  done
  rm -rf "${INC_TMP}"
done
SEG[PG恢复]=$(($(now_s) - T0))

# ---- 3. Elasticsearch 恢复 --------------------------------------------------------
T0=$(now_s)
if docker ps --format '{{.Names}}' | grep -q "^${ES_CONTAINER}$"; then
  curl -sf -X PUT "${ES_URL}/_snapshot/${ES_REPO_NAME}" \
    -H 'Content-Type: application/json' \
    -d "{\"type\":\"fs\",\"settings\":{\"location\":\"${ES_REPO_PATH}\"}}" > /dev/null \
    || die "ES 快照仓库注册失败（path.repo=${ES_REPO_PATH} 未在 ES 侧配置？）"

  if [ -z "${ES_SNAPSHOT:-}" ] && [ -n "${TS:-}" ]; then
    if curl -sf "${ES_URL}/_snapshot/${ES_REPO_NAME}/snapshot_${TS}" > /dev/null 2>&1; then
      ES_SNAPSHOT="snapshot_${TS}"
    fi
  fi
  if [ -z "${ES_SNAPSHOT:-}" ]; then
    ES_SNAPSHOT=$(curl -sf "${ES_URL}/_snapshot/${ES_REPO_NAME}/_all" \
      | grep -o '"snapshot":"[^"]*"' | cut -d'"' -f4 | sort | tail -n1)
    [ -n "${ES_SNAPSHOT}" ] || die "快照仓库 ${ES_REPO_NAME} 中没有可用快照"
    log "  未指定快照，使用最新: ${ES_SNAPSHOT}"
  fi
  # 校验指定快照存在且状态可用
  SNAP_INFO=$(curl -sf "${ES_URL}/_snapshot/${ES_REPO_NAME}/${ES_SNAPSHOT}") \
    || die "指定快照不存在: ${ES_SNAPSHOT}"
  echo "${SNAP_INFO}" | tr -d '[:space:]' | grep -q '"state":"SUCCESS"' \
    || die "指定快照状态非 SUCCESS: ${SNAP_INFO}"

  curl -sf -X POST "${ES_URL}/${ES_INDICES}/_close?ignore_unavailable=true" > /dev/null \
    || die "关闭索引失败: ${ES_INDICES}"
  curl -sf -X POST "${ES_URL}/_snapshot/${ES_REPO_NAME}/${ES_SNAPSHOT}/_restore?wait_for_completion=true" \
    -H 'Content-Type: application/json' \
    -d "{\"indices\":\"${ES_INDICES}\",\"ignore_unavailable\":true}" > /dev/null \
    || die "快照恢复失败: ${ES_SNAPSHOT}"
  curl -sf -X POST "${ES_URL}/${ES_INDICES}/_open?ignore_unavailable=true" > /dev/null \
    || die "打开索引失败: ${ES_INDICES}"

  DOCS=$(curl -sf "${ES_URL}/_cat/indices/${ES_INDICES}?format=json" \
    | grep -o '"docs.count":"[0-9]*"' | grep -o '[0-9]*' | head -n1 || true)
  [ -n "${DOCS}" ] && [ "${DOCS}" -gt 0 ] \
    || die "ES 恢复校验失败: ${ES_INDICES} 文档数为 ${DOCS:-空}"
  log "  ES 恢复完成: ${ES_SNAPSHOT}（${ES_INDICES} 文档数=${DOCS}）"
else
  log "  [警告] 未发现运行中的 ES 容器 ${ES_CONTAINER}，跳过 ES 恢复"
fi
SEG[ES恢复]=$(($(now_s) - T0))

# ---- 4. Redis 恢复 ----------------------------------------------------------------
T0=$(now_s)
REDIS_FILE="${REDIS_BACKUP:-}"
if [ -z "${REDIS_FILE}" ] && [ -n "${TS:-}" ]; then
  for cand in "${BACKUP_DIR}/redis-${TS}.tar.gz.enc" "${BACKUP_DIR}/redis-${TS}.tar.gz"; do
    [ -f "${cand}" ] && REDIS_FILE="${cand}" && break
  done
fi
if [ -z "${REDIS_FILE}" ]; then
  REDIS_FILE=$(ls -1 "${BACKUP_DIR}"/redis-*.tar.gz* 2>/dev/null | sort | tail -n1 || true)
fi
if [ -z "${REDIS_FILE}" ]; then
  log "  [警告] 未找到 Redis 备份文件，跳过 Redis 恢复（队列/缓存将重建）"
else
  REDIS_TMP=$(mktemp -d)
  decrypt_cat "${REDIS_FILE}" | tar -xzf - -C "${REDIS_TMP}"
  [ -f "${REDIS_TMP}/dump.rdb" ] || die "Redis 备份包内缺少 dump.rdb: ${REDIS_FILE}"

  docker compose -f "${COMPOSE_FILE}" stop redis > /dev/null
  docker ps --format '{{.Names}}' | grep -q "^${REDIS_CONTAINER}$" \
    && die "Redis 容器停止失败，中止恢复"

  # 清空数据卷内旧 RDB/AOF（AOF 开启时 Redis 启动优先读 AOF，不清掉会使 RDB 恢复无效）；
  # 复用 compose 已有的 redis:7-alpine 镜像操作数据卷，避免额外拉取镜像
  docker run --rm -v agendascope_redisdata:/data redis:7-alpine \
    sh -c 'rm -rf /data/appendonlydir /data/dump.rdb'
  docker cp "${REDIS_TMP}/dump.rdb" "${REDIS_CONTAINER}:/data/dump.rdb"
  if [ -d "${REDIS_TMP}/appendonlydir" ]; then
    docker cp "${REDIS_TMP}/appendonlydir" "${REDIS_CONTAINER}:/data/appendonlydir"
  fi
  rm -rf "${REDIS_TMP}"

  docker compose -f "${COMPOSE_FILE}" start redis > /dev/null
  PING_OK=0
  for _ in $(seq 1 30); do
    sleep 2
    if docker exec "${REDIS_CONTAINER}" redis-cli PING 2>/dev/null | grep -q PONG; then
      PING_OK=1
      break
    fi
  done
  [ "${PING_OK}" = "1" ] || die "Redis 启动后 PING 无响应"
  DBSIZE=$(docker exec "${REDIS_CONTAINER}" redis-cli DBSIZE | tr -d '[:space:]')
  log "  Redis 恢复完成（PING=PONG, DBSIZE=${DBSIZE}）"
fi
SEG[Redis恢复]=$(($(now_s) - T0))

# ---- 5. 恢复后校验 -----------------------------------------------------------------
T0=$(now_s)
for t in articles topics agenda_events; do
  CNT=$(psqlc -tAc "SELECT count(*) FROM ${t}" | tr -d '[:space:]')
  [ -n "${CNT}" ] && [ "${CNT}" -gt 0 ] \
    || die "PG 校验失败: ${t} 行数为 ${CNT:-空}（恢复结果不可用，写入方保持停止）"
  log "  PG 校验 ${t}: ${CNT} 行"
done

# 先起 backend 做健康校验（/health 路由挂在根路径，不在 /api/v1 前缀下）
docker compose -f "${COMPOSE_FILE}" start backend > /dev/null
HEALTH_OK=0
for _ in $(seq 1 45); do
  sleep 2
  if curl -sf "http://localhost:8000/health" > /dev/null 2>&1; then
    HEALTH_OK=1
    break
  fi
done
[ "${HEALTH_OK}" = "1" ] || die "后端 /health 90s 内未返回 200（PG/Redis/ES 组件探活未全绿），写入方保持停止"
log "  后端 /health 200 OK"
SEG[校验]=$(($(now_s) - T0))

# ---- 6. 恢复写入方 ------------------------------------------------------------------
T0=$(now_s)
for svc in worker nlp-worker cluster-worker naming-worker agenda-worker snapshot-worker; do
  docker compose -f "${COMPOSE_FILE}" start "${svc}" > /dev/null
  log "  已启动: ${svc}"
done
SEG[恢复服务]=$(($(now_s) - T0))

# ---- 7. RTO 报告 --------------------------------------------------------------------
T_TOTAL=$(($(now_s) - T_START))
log "恢复完成。分段耗时："
for seg in 停写 PG恢复 ES恢复 Redis恢复 校验 恢复服务; do
  printf '  %-10s %4d s\n' "${seg}" "${SEG[$seg]}"
done
printf '  %-10s %4d s（RTO 目标 ≤%d s）\n' "总计" "${T_TOTAL}" "${RTO_TARGET_SECONDS}"
if [ "${T_TOTAL}" -gt "${RTO_TARGET_SECONDS}" ]; then
  log "[警告] 本次恢复超出 RTO 目标（30 min），请在演练记录中分析原因"
fi
