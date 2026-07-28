#!/usr/bin/env bash
# AgendaScope 观澜 — 备份脚本（Phase 5 T5.11）
#
# 产物（均落在 ${BACKUP_DIR}）：
#   全量 PG : agendascope-<ts>.sql.gz[.enc]          pg_dump | gzip | AES-256
#   增量 PG : agendascope-inc-<ts>.tar.gz[.enc]      核心表按水位线差分 COPY 出 CSV 后打包
#   Redis   : redis-<ts>.tar.gz[.enc]                dump.rdb + appendonlydir
#   ES      : snapshot_<ts>（ES 快照仓库内，名称记录到 .last_es_snapshot）
#   每个文件产物附 .sha256 校验文件
#
# 环境变量：
#   BACKUP_DIR        备份目录（默认 $HOME/agendascope/backups）
#   RETENTION_DAYS    本地产物保留天数（默认 30）
#   BACKUP_MODE       full（默认）| incremental（按 ${BACKUP_DIR}/.last_backup_ts 水位线差分）
#   BACKUP_ENCRYPT    1（默认）AES-256 加密；0 明文，仅限调试，会有醒目警告
#   BACKUP_KEY_FILE   加密密钥文件路径（BACKUP_ENCRYPT=1 时必填）。
#                     密钥由部署方生成并离线保管：openssl rand -base64 32 > /secure/path/backup.key
#   ES_URL            默认 http://localhost:9200
#   ES_REPO_NAME      快照仓库名（默认 backup）
#   ES_REPO_PATH      快照仓库 location（默认 /usr/share/elasticsearch/backups）。
#                     【部署要求】deploy/docker-compose.yml 由其他负责人维护，本脚本不修改；
#                     部署侧必须为 elasticsearch 服务配置：
#                       environment: path.repo: /usr/share/elasticsearch/backups
#                       volumes: 挂载一个宿主机目录到 /usr/share/elasticsearch/backups
#                     否则仓库注册会失败，本脚本将按设计报错退出（不允许假成功）。
#   PG_CONTAINER / REDIS_CONTAINER / ES_CONTAINER  容器名覆盖
#
# 失败告警：任何步骤失败 → trap ERR 累计 ${BACKUP_DIR}/.backup_fail_count；
#   连续失败 >=2 次时向 alerts 表真实写入一条 P1 告警（字段对齐 backend/app/models/alert.py）。
#
# 兼容性：目标运行环境为 Linux 备份主机；Windows Git Bash 下亦可执行（不用 -t 分配 TTY、
#   date 仅用 GNU 语法、路径一律引号包裹）；macOS 的 BSD date 不支持 -d，不保证可用。
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-$HOME/agendascope/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
BACKUP_MODE="${BACKUP_MODE:-full}"
BACKUP_ENCRYPT="${BACKUP_ENCRYPT:-1}"
ES_URL="${ES_URL:-http://localhost:9200}"
ES_REPO_NAME="${ES_REPO_NAME:-backup}"
ES_REPO_PATH="${ES_REPO_PATH:-/usr/share/elasticsearch/backups}"
PG_CONTAINER="${PG_CONTAINER:-agendascope-db-1}"
REDIS_CONTAINER="${REDIS_CONTAINER:-agendascope-redis-1}"
ES_CONTAINER="${ES_CONTAINER:-agendascope-elasticsearch-1}"

DATE=$(date +%Y%m%d-%H%M%S)
FAIL_COUNT_FILE="${BACKUP_DIR}/.backup_fail_count"
WATERMARK_FILE="${BACKUP_DIR}/.last_backup_ts"

mkdir -p "${BACKUP_DIR}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

psqlc() { docker exec "${PG_CONTAINER}" psql -U agenda -d agendascope "$@"; }

# ---- 连续失败告警：字段逐一对齐 backend/app/models/alert.py -----------------
# alerts 必填/约束：id(UUID)、rule_id(FK alert_rules.id)、user_id(FK users.id)、
#   payload(JSONB 必填)、status ∈ ('unread','read','archived','suppressed')。
# alert_rules 约束：condition_type ∈ ('growth_rate','top_n','neg_ratio')、
#   active_period ∈ ('all_day','custom')、ck_rules_target: topic_id 或 keywords 至少其一。
insert_p1_alert() {
  local fail_count="$1" reason="$2"
  # 告警写入失败不允许级联（调用处已用 || 包裹），只记录日志
  if ! docker exec "${PG_CONTAINER}" pg_isready -U agenda -d agendascope >/dev/null 2>&1; then
    log "  [告警] 数据库不可用，跳过 P1 告警写入（失败原因已记录到本地计数）"
    return 0
  fi
  # reason 清洗：去掉双引号/反斜杠/换行，截断 200 字符，保证 payload JSON 合法
  reason=$(printf '%s' "${reason}" | tr -d '"\\\r\n' | head -c 200)

  local uid rid payload
  uid=$(psqlc -tAc "SELECT id FROM users WHERE role='admin' AND status='active' ORDER BY created_at LIMIT 1" 2>/dev/null | head -n1 | tr -d '[:space:]')
  if [ -z "${uid}" ]; then
    uid=$(psqlc -tAc "SELECT id FROM users WHERE status='active' ORDER BY created_at LIMIT 1" 2>/dev/null | head -n1 | tr -d '[:space:]')
  fi
  if [ -z "${uid}" ]; then
    log "  [告警] 库中无可用用户，无法写入 alerts（user_id 为必填 FK），仅记录日志"
    return 0
  fi

  # 查找/创建系统备份告警规则（alerts.rule_id 为必填 FK，必须先有规则）
  rid=$(psqlc -tAc "SELECT id FROM alert_rules WHERE name='系统备份失败告警' ORDER BY created_at LIMIT 1" 2>/dev/null | head -n1 | tr -d '[:space:]')
  if [ -z "${rid}" ]; then
    rid=$(psqlc -tAc "INSERT INTO alert_rules (id, user_id, name, country_codes, keywords, condition_type, condition_value, active_period, notify_channels, enabled)
      VALUES (gen_random_uuid(), '${uid}', '系统备份失败告警', '[]'::jsonb, '[\"backup\"]'::jsonb, 'top_n', 1, 'all_day', '[\"inapp\"]'::jsonb, true)
      RETURNING id" 2>/dev/null | head -n1 | tr -d '[:space:]')
  fi
  if [ -z "${rid}" ]; then
    log "  [告警] 系统告警规则创建失败，仅记录日志"
    return 0
  fi

  payload=$(printf '{"severity":"P1","source":"scripts/backup.sh","title":"备份连续失败","message":"备份已连续失败 %s 次，最近失败原因：%s","fail_count":%s,"backup_dir":"%s"}' \
    "${fail_count}" "${reason}" "${fail_count}" "${BACKUP_DIR}")
  psqlc -q -c "INSERT INTO alerts (id, rule_id, user_id, payload, status, suppressed_count)
    VALUES (gen_random_uuid(), '${rid}', '${uid}', '${payload}'::jsonb, 'unread', 0)" 2>/dev/null \
    && log "  [告警] 已写入 P1 备份失败告警（连续失败 ${fail_count} 次）"
}

# ---- 失败处理：trap ERR 捕获任何失败命令 ------------------------------------
on_failure() {
  local reason="${1:-未知失败}"
  trap - ERR   # 防递归
  log "[失败] ${reason}"
  local n
  n=$(cat "${FAIL_COUNT_FILE}" 2>/dev/null || echo 0)
  n=$((n + 1))
  echo "${n}" > "${FAIL_COUNT_FILE}"
  log "  连续失败次数: ${n}"
  if [ "${n}" -ge 2 ]; then
    insert_p1_alert "${n}" "${reason}" || log "  [告警] P1 告警写入异常（不级联失败）"
  fi
  exit 1
}
# $LINENO 保留在单引号内，trap 触发时才展开为失败命令所在行
trap 'on_failure "命令执行失败（脚本第 $LINENO 行附近）"' ERR

die() { on_failure "$*"; }

# ---- 加密 --------------------------------------------------------------------
if [ "${BACKUP_ENCRYPT}" = "1" ]; then
  [ -n "${BACKUP_KEY_FILE:-}" ] || die "BACKUP_KEY_FILE 未设置。生成方法：openssl rand -base64 32 > <密钥文件>（部署方离线保管）"
  [ -f "${BACKUP_KEY_FILE}" ] || die "BACKUP_KEY_FILE 指定的密钥文件不存在: ${BACKUP_KEY_FILE}"
  ENC_SUFFIX=".enc"
else
  ENC_SUFFIX=""
  log "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  log "!! 警告：BACKUP_ENCRYPT=0，备份产物为明文，仅限调试环境使用      !!"
  log "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
fi

encrypt_stream() {
  if [ "${BACKUP_ENCRYPT}" = "1" ]; then
    openssl enc -aes-256-cbc -pbkdf2 -salt -pass file:"${BACKUP_KEY_FILE}"
  else
    cat
  fi
}

write_sha256() {
  # 产物完整性校验文件（演练前置检查使用）；sha256sum 失败视为产物不可信，不容错
  (cd "${BACKUP_DIR}" && sha256sum "$(basename "$1")" > "$1.sha256")
}

log "开始备份（模式=${BACKUP_MODE}，加密=${BACKUP_ENCRYPT}，目录=${BACKUP_DIR}）"

# ---- 1. PostgreSQL -----------------------------------------------------------
if [ "${BACKUP_MODE}" = "incremental" ] && [ -f "${WATERMARK_FILE}" ]; then
  WM=$(cat "${WATERMARK_FILE}")
  # 新水位线取自数据库时钟，避免备份主机与 DB 时钟偏差（仅去换行，保留日期与时间间空格）
  NEW_WM=$(psqlc -tAc "SELECT now()" | tr -d '\r\n')
  [ -n "${NEW_WM}" ] || die "无法从数据库获取当前时间作为新水位线"

  INC_TMP=$(mktemp -d)
  trap 'rm -rf "${INC_TMP}"; on_failure "命令执行失败"' ERR
  # 核心表差分导出。列以 backend/app/models/ 为准（SELECT * 顺序即表定义顺序）：
  #   articles(id,source_id,url,url_hash,title,...,created_at)      水位列: created_at（无 updated_at）
  #   topics(...,created_at,updated_at)                             水位列: created_at OR updated_at
  #   topic_articles(topic_id,article_id,weight,assign_method,assigned_at)  水位列: assigned_at
  #   agenda_events(...,created_at,updated_at)                      水位列: created_at OR updated_at
  #   agenda_event_evidence(...,created_at)                         水位列: created_at
  #   agenda_snapshots(...,created_at)                              水位列: created_at
  #   alerts(...,created_at)                                        水位列: created_at
  #   alert_rules(...,created_at,updated_at)                        水位列: created_at OR updated_at
  declare -A INC_WHERE=(
    [articles]="created_at > '${WM}'"
    [topics]="created_at > '${WM}' OR updated_at > '${WM}'"
    [topic_articles]="assigned_at > '${WM}'"
    [agenda_events]="created_at > '${WM}' OR updated_at > '${WM}'"
    [agenda_event_evidence]="created_at > '${WM}'"
    [agenda_snapshots]="created_at > '${WM}'"
    [alerts]="created_at > '${WM}'"
    [alert_rules]="created_at > '${WM}' OR updated_at > '${WM}'"
  )
  INC_TABLES="articles topics topic_articles agenda_events agenda_event_evidence agenda_snapshots alerts alert_rules"
  : > "${INC_TMP}/manifest.txt"
  echo "watermark_from=${WM}" >> "${INC_TMP}/manifest.txt"
  echo "watermark_to=${NEW_WM}" >> "${INC_TMP}/manifest.txt"
  for t in ${INC_TABLES}; do
    psqlc -c "COPY (SELECT * FROM ${t} WHERE ${INC_WHERE[$t]}) TO STDOUT WITH (FORMAT csv, HEADER true)" \
      > "${INC_TMP}/${t}.csv"
    rows=$(( $(wc -l < "${INC_TMP}/${t}.csv") - 1 ))
    [ "${rows}" -lt 0 ] && rows=0
    echo "${t}=${rows}" >> "${INC_TMP}/manifest.txt"
    log "  增量导出 ${t}: ${rows} 行"
  done
  INC_FILE="${BACKUP_DIR}/agendascope-inc-${DATE}.tar.gz${ENC_SUFFIX}"
  tar -c -C "${INC_TMP}" . | gzip | encrypt_stream > "${INC_FILE}"
  rm -rf "${INC_TMP}"
  trap 'on_failure "命令执行失败"' ERR
  # 全部导出成功后才推进水位线
  echo "${NEW_WM}" > "${WATERMARK_FILE}"
  write_sha256 "${INC_FILE}"
  log "  PG 增量备份: ${INC_FILE} ($(du -h "${INC_FILE}" | cut -f1))"
else
  if [ "${BACKUP_MODE}" = "incremental" ]; then
    log "  未找到水位线文件 ${WATERMARK_FILE}，自动降级为全量备份"
  fi
  PG_FILE="${BACKUP_DIR}/agendascope-${DATE}.sql.gz${ENC_SUFFIX}"
  docker exec "${PG_CONTAINER}" pg_dump -U agenda agendascope | gzip | encrypt_stream > "${PG_FILE}"
  write_sha256 "${PG_FILE}"
  # 全量后备份水位线重置为 DB 当前时间，后续增量以此为基线
  psqlc -tAc "SELECT now()" | tr -d '\r\n' > "${WATERMARK_FILE}"
  log "  PG 全量备份: ${PG_FILE} ($(du -h "${PG_FILE}" | cut -f1))"
fi

# ---- 2. Elasticsearch 快照 ----------------------------------------------------
if docker ps --format '{{.Names}}' | grep -q "^${ES_CONTAINER}$"; then
  # 注册快照仓库：失败立即报错退出（部署要求见脚本头部注释，path.repo 须在 compose 侧配置）
  curl -sf -X PUT "${ES_URL}/_snapshot/${ES_REPO_NAME}" \
    -H 'Content-Type: application/json' \
    -d "{\"type\":\"fs\",\"settings\":{\"location\":\"${ES_REPO_PATH}\"}}" > /dev/null \
    || die "ES 快照仓库注册失败（检查 ES 容器 path.repo=${ES_REPO_PATH} 配置）"
  log "  ES 快照仓库已注册: ${ES_REPO_NAME} -> ${ES_REPO_PATH}"

  SNAP_NAME="snapshot_${DATE}"
  SNAP_RESP=$(curl -sf -X PUT "${ES_URL}/_snapshot/${ES_REPO_NAME}/${SNAP_NAME}?wait_for_completion=true" \
    -H 'Content-Type: application/json' \
    -d '{"ignore_unavailable":true,"include_global_state":false}') \
    || die "ES 快照请求失败: ${SNAP_NAME}"
  echo "${SNAP_RESP}" | tr -d '[:space:]' | grep -q '"state":"SUCCESS"' \
    || die "ES 快照状态非 SUCCESS: ${SNAP_RESP}"
  echo "${SNAP_NAME}" > "${BACKUP_DIR}/.last_es_snapshot"
  log "  ES 快照: ${SNAP_NAME}（state=SUCCESS）"

  # 旧快照清理（纯维护操作，允许容错：单个删除失败不阻断备份，仅告警日志）
  CUTOFF=$(date -d "${RETENTION_DAYS} days ago" +%Y%m%d-%H%M%S 2>/dev/null || echo "")
  if [ -n "${CUTOFF}" ]; then
    OLD_SNAPS=$(curl -sf "${ES_URL}/_snapshot/${ES_REPO_NAME}/_all" 2>/dev/null | grep -o '"snapshot":"[^"]*"' | cut -d'"' -f4 || true)
    for s in ${OLD_SNAPS}; do
      d="${s#snapshot_}"
      if [ "${d}" \< "${CUTOFF}" ]; then
        curl -sf -X DELETE "${ES_URL}/_snapshot/${ES_REPO_NAME}/${s}" > /dev/null \
          && log "  已清理旧快照: ${s}" || log "  [警告] 旧快照删除失败（维护操作，继续）: ${s}"
      fi
    done
  fi
else
  log "  [警告] 未发现运行中的 ES 容器 ${ES_CONTAINER}，跳过 ES 快照（PG/Redis 备份不受影响）"
fi

# ---- 3. Redis（BGSAVE 轮询落盘确认后拷出） -------------------------------------
PREV_SAVE=$(docker exec "${REDIS_CONTAINER}" redis-cli LASTSAVE | tr -d '[:space:]')
# BGSAVE 正常返回 "Background saving started"；若已有保存进行中则返回错误文本但仍会落盘，均继续轮询
docker exec "${REDIS_CONTAINER}" redis-cli BGSAVE | grep -qiE "saving started|already in progress" \
  || die "Redis BGSAVE 未正常启动"
SAVE_OK=0
for _ in $(seq 1 60); do
  sleep 2
  CUR_SAVE=$(docker exec "${REDIS_CONTAINER}" redis-cli LASTSAVE | tr -d '[:space:]')
  if [ -n "${CUR_SAVE}" ] && [ "${CUR_SAVE}" != "${PREV_SAVE}" ]; then
    SAVE_OK=1
    break
  fi
done
[ "${SAVE_OK}" = "1" ] || die "Redis BGSAVE 120s 内未落盘（LASTSAVE 未变化）"

REDIS_TMP=$(mktemp -d)
docker cp "${REDIS_CONTAINER}:/data/dump.rdb" "${REDIS_TMP}/dump.rdb"
# appendonlydir 仅在 AOF 开启时存在（compose 默认 appendonly yes）；缺失时告警但不阻断，
# 因为 RDB 已足以恢复（AOF 缺失只丢失最后一次 BGSAVE 后的写入，属可接受窗口）
if ! docker cp "${REDIS_CONTAINER}:/data/appendonlydir" "${REDIS_TMP}/appendonlydir" 2>/dev/null; then
  log "  [警告] 未发现 Redis appendonlydir（AOF 未开启？），仅备份 RDB"
fi
REDIS_FILE="${BACKUP_DIR}/redis-${DATE}.tar.gz${ENC_SUFFIX}"
tar -c -C "${REDIS_TMP}" . | gzip | encrypt_stream > "${REDIS_FILE}"
rm -rf "${REDIS_TMP}"
write_sha256 "${REDIS_FILE}"
log "  Redis 备份: ${REDIS_FILE} ($(du -h "${REDIS_FILE}" | cut -f1))"

# ---- 4. 本地旧产物清理（纯维护操作，允许容错：find 在 Git Bash 下 -mtime 行为一致，
#         单个删除失败不影响本次备份有效性） --------------------------------------
find "${BACKUP_DIR}" -maxdepth 1 \( -name "agendascope-*.sql.gz*" -o -name "agendascope-inc-*.tar.gz*" -o -name "redis-*.tar.gz*" \) \
  -mtime "+${RETENTION_DAYS}" -delete 2>/dev/null || log "  [警告] 旧备份清理部分失败（维护操作，继续）"

# ---- 成功收尾：失败计数归零 ----------------------------------------------------
echo 0 > "${FAIL_COUNT_FILE}"
log "备份完成。保留最近 ${RETENTION_DAYS} 天，失败计数已归零。"
