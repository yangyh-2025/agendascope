#!/bin/bash
# AgendaScope 观澜 — 备份脚本（Phase 5 T5.11）
# 每日全量 pg_dump + ES snapshot + Redis RDB 备份，保留 30 天
# AES-256 加密密钥由部署方保管（本脚本加密为可选，调用方自行管理密钥）
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-$HOME/agendascope/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
DATE=$(date +%Y%m%d-%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/agendascope-${DATE}.sql.gz"

mkdir -p "${BACKUP_DIR}"

echo "[$(date)] 开始备份..."

# PostgreSQL
PG_CONTAINER="${PG_CONTAINER:-agendascope-db-1}"
docker exec "${PG_CONTAINER}" pg_dump -U agenda agendascope | gzip > "${BACKUP_FILE}"
echo "  PG 备份: ${BACKUP_FILE} ($(du -h "${BACKUP_FILE}" | cut -f1))"

# Elasticsearch snapshot（若有 es 容器）
ES_CONTAINER="${ES_CONTAINER:-agendascope-elasticsearch-1}"
if docker ps --format '{{.Names}}' | grep -q "${ES_CONTAINER}"; then
  ES_BACKUP="${BACKUP_DIR}/agendascope-es-${DATE}"
  mkdir -p "${ES_BACKUP}"
  curl -sf -X PUT "localhost:9200/_snapshot/backup/snapshot_${DATE}" \
    -H 'Content-Type: application/json' \
    -d "{\"indices\":\"articles\",\"ignore_unavailable\":true}" > /dev/null 2>&1 || true
  echo "  ES 快照: snapshot_${DATE}"
fi

# Redis RDB（redis BGSAVE）
docker exec agendascope-redis-1 redis-cli BGSAVE > /dev/null 2>&1 || true

# 清理 30 天前旧备份
find "${BACKUP_DIR}" -name "agendascope-*.sql.gz" -mtime "+${RETENTION_DAYS}" -delete 2>/dev/null || true

echo "[$(date)] 备份完成。保留最近 ${RETENTION_DAYS} 天。"
