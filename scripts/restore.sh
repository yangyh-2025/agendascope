#!/bin/bash
# AgendaScope 观澜 — 恢复脚本（Phase 5 T5.11）
# 用法: bash scripts/restore.sh <备份文件路径.sql.gz>
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "用法: bash scripts/restore.sh <备份文件路径.sql.gz>"
  echo "  RTO ≤30 min（目标），实际时间取决于数据量"
  exit 1
fi

BACKUP_FILE="$1"
if [ ! -f "${BACKUP_FILE}" ]; then
  echo "[错误] 备份文件不存在: ${BACKUP_FILE}"
  exit 1
fi

PG_CONTAINER="${PG_CONTAINER:-agendascope-db-1}"
echo "[$(date)] 开始恢复: ${BACKUP_FILE}"

gunzip -c "${BACKUP_FILE}" | docker exec -i "${PG_CONTAINER}" psql -U agenda agendascope

echo "[$(date)] 恢复完成。"
echo "  建议重启后端服务: docker compose -f deploy/docker-compose.yml restart backend"
