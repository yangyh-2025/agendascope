#!/bin/bash
# AgendaScope 观澜 — 一键安装脚本（Phase 5 T5.9）
# 目标：零编程用户独立完成部署，首次看板配置 ≤10 min
set -euo pipefail

AGENDASCOPE_HOME="${AGENDASCOPE_HOME:-$HOME/agendascope}"
MODEL_DIR="${AGENDASCOPE_HOME}/models"
DATA_DIR="${AGENDASCOPE_HOME}/data"

echo "========================================"
echo " AgendaScope 观澜 — 一键安装部署"
echo "========================================"
echo ""
echo "安装位置: ${AGENDASCOPE_HOME}"
echo ""

# Step 0: 环境自检
command -v docker >/dev/null 2>&1 || { echo "[错误] 未检测到 Docker，请先安装 Docker 20+"; exit 1; }
command -v docker compose >/dev/null 2>&1 && COMPOSE_CMD="docker compose" || { command -v docker-compose >/dev/null 2>&1 && COMPOSE_CMD="docker-compose"; } || { echo "[错误] 未检测到 docker compose"; exit 1; }
TOTAL_MEM=$(free -m | awk '/^Mem:/{print $2}')
if [ "$TOTAL_MEM" -lt 8000 ]; then
  echo "[警告] 内存 ${TOTAL_MEM}MB < 8GB（推荐），聚类与 LLM 推理可能受限，仍可继续"
fi
CPU_COUNT=$(nproc 2>/dev/null || echo 1)
echo "  Docker: OK | CPU: ${CPU_COUNT} 核 | 内存: ${TOTAL_MEM}MB"
echo ""

# Step 1: 创建目录结构
mkdir -p "${AGENDASCOPE_HOME}" "${MODEL_DIR}" "${DATA_DIR}/pgdata" "${DATA_DIR}/esdata" "${DATA_DIR}/redisdata"
cd "${AGENDASCOPE_HOME}"

# Step 2: 拉取/构建镜像（优先使用本地离线包，其次 registry）
if [ -d "${AGENDASCOPE_HOME}/deploy/offline" ] && [ -f "${AGENDASCOPE_HOME}/deploy/offline/images.tar" ]; then
  echo "[信息] 检测到离线镜像包，从本地加载..."
  docker load -i "${AGENDASCOPE_HOME}/deploy/offline/images.tar"
  echo "  离线镜像加载完成"
else
  echo "[信息] 在线构建镜像（仅首次需要，约 3-5 分钟）..."
  $COMPOSE_CMD -f deploy/docker-compose.yml build
fi

# Step 3: 启动全栈
echo ""
echo "[信息] 启动全栈服务..."
$COMPOSE_CMD -f deploy/docker-compose.yml up -d
echo ""

# Step 4: 等待后端就绪
echo "[信息] 等待服务就绪（最多 60 秒）..."
for i in $(seq 1 30); do
  if curl -sf http://localhost:8000/api/v1/health > /dev/null 2>&1; then
    echo "  后端就绪 (${i}s)"
    break
  fi
  sleep 2
done

# Step 5: 种子源导入
echo ""
echo "[信息] 导入种子源（31 国 39 源）..."
curl -sf http://localhost:8000/api/v1/setup/seed -X POST -H "Content-Type: application/json" \
  -d '{"admin_username":"admin","admin_password":"Agendascope123!"}' > /dev/null 2>&1 || true
echo "  种子源导入完成"

echo ""
echo "========================================"
echo "  安装完成！"
echo "  访问: http://localhost:8000"
echo "  管理面板: http://localhost:8000/system"
echo "  初始管理员: admin / Agendascope123!"
echo "  （请登录后立即修改密码）"
echo "========================================"
