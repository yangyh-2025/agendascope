#!/bin/bash
# AgendaScope 观澜 — 完全离线安装包构建脚本（Phase 5 T5.8）
#
# 用途：在【可联网构建机】上执行一次，产出可拷贝到离线/内网环境直接安装的交付包。
#   安装侧由 scripts/install.sh 识别（探测 <包根>/images/*.tar + SHA256SUMS，
#   sha256sum -c 校验后 docker load，跳过一切外网拉取）。
#
# 用法：
#   bash scripts/build_offline_package.sh            # 构建
#   bash scripts/build_offline_package.sh --verify [包目录]   # 校验已有包完整性
#
# 可配置环境变量：
#   OUTPUT_DIR   输出目录（默认 <仓库根>/deploy/offline）
#   MODELS_DIR   模型权重目录（默认 <仓库根>/models）
#   SKIP_BUILD=1 跳过 compose build（复用本机已有镜像时）
#
# 产物布局：
#   ${OUTPUT_DIR}/agendascope-offline/
#     install.sh              安装入口（scripts/install.sh 的副本，INSTALL_MODE=local 即可用）
#     images/agendascope-images.tar   全部服务镜像（backend + db/redis/es/rsshub）
#     images/SHA256SUMS       镜像 tar 校验清单（install.sh 离线模式校验此文件）
#     models/                 fastText lid.176.bin（必需）；sentence-transformers/Qwen 仅本地模式可选
#     models/argos/           argos 翻译语言包（若构建机存在；缺失则警告，需自行下载放入）
#     maps/                   前端地图/tiles 资产（若源码树内存在；缺失则警告）
#     deploy/ scripts/ backend/ frontend/   源码（frontend 排除 node_modules/dist 等构建缓存）
#     SHA256SUMS              全包逐文件校验清单（--verify 校验此文件）
#   ${OUTPUT_DIR}/agendascope-offline-<日期>.tar.gz(.sha256)   整包压缩归档，便于一次性拷贝
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/deploy/offline}"
MODELS_DIR="${MODELS_DIR:-${REPO_ROOT}/models}"
COMPOSE_FILE="${REPO_ROOT}/deploy/docker-compose.yml"
PKG_NAME="agendascope-offline"

die() { echo "[错误] $*" >&2; exit 1; }
info() { echo "[信息] $*"; }
warn() { echo "[警告] $*"; }

# ---------- --verify：校验已有离线包 ----------
if [ "${1:-}" = "--verify" ]; then
  PKG_DIR="${2:-${OUTPUT_DIR}/${PKG_NAME}}"
  [ -f "${PKG_DIR}/SHA256SUMS" ] || die "未找到 ${PKG_DIR}/SHA256SUMS"
  info "校验离线包: ${PKG_DIR}"
  (cd "${PKG_DIR}" && sha256sum -c SHA256SUMS) || die "离线包校验失败"
  echo "  离线包校验通过"
  exit 0
fi

# ---------- 前置检查 ----------
command -v docker >/dev/null 2>&1 || die "未检测到 Docker"
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  die "未检测到 docker compose"
fi
[ -f "${COMPOSE_FILE}" ] || die "未找到 ${COMPOSE_FILE}"

# 模型权重：lid.176.bin 为硬需求（语言识别，nlp-worker 卷挂载，无 API 替代）；
# sentence-transformers/Qwen 仅本地嵌入/LLM 模式需要，云 API 模式可缺失（提示而非报错）
[ -e "${MODELS_DIR}/lid.176.bin" ] || die "缺少模型文件 ${MODELS_DIR}/lid.176.bin（可用 MODELS_DIR 指定模型目录）"
for optional in sentence-transformers Qwen2.5-0.5B-Instruct; do
  [ -e "${MODELS_DIR}/${optional}" ] || info "未发现 ${MODELS_DIR}/${optional} —— 云 API 模式（LLM_PROFILE=api / NLP_EMBEDDING_PROFILE=api）下不需要本地权重"
done

STAGE="${OUTPUT_DIR}/${PKG_NAME}"
rm -rf "${STAGE}"
mkdir -p "${STAGE}/images" "${STAGE}/models" "${STAGE}/maps"

# ---------- Step 1: 构建镜像 ----------
if [ "${SKIP_BUILD:-0}" != "1" ]; then
  info "构建服务镜像（compose build）..."
  "${COMPOSE[@]}" -f "${COMPOSE_FILE}" build || die "镜像构建失败"
else
  info "SKIP_BUILD=1，跳过 compose build"
fi

# ---------- Step 2: 收集并导出全部镜像 ----------
# config --images 同时列出构建产物（agendascope-backend）与基础镜像
# （pgvector/pgvector、redis、elasticsearch、rsshub），本地缺失的先 pull 再一并打包。
info "收集镜像清单（compose config --images）..."
mapfile -t IMAGES < <("${COMPOSE[@]}" -f "${COMPOSE_FILE}" config --images | sort -u)
[ "${#IMAGES[@]}" -gt 0 ] || die "compose config --images 返回空清单"

for img in "${IMAGES[@]}"; do
  if ! docker image inspect "${img}" >/dev/null 2>&1; then
    info "本地缺少 ${img}，执行 docker pull ..."
    docker pull "${img}" || die "拉取镜像失败: ${img}"
  fi
done

info "导出 ${#IMAGES[@]} 个镜像到 images/agendascope-images.tar ..."
printf '  - %s\n' "${IMAGES[@]}"
docker save -o "${STAGE}/images/agendascope-images.tar" "${IMAGES[@]}" || die "docker save 失败"
(cd "${STAGE}/images" && sha256sum agendascope-images.tar > SHA256SUMS)

# ---------- Step 3: 打包模型权重 ----------
info "打包模型权重: ${MODELS_DIR}"
cp -a "${MODELS_DIR}/lid.176.bin" "${STAGE}/models/"
for optional in sentence-transformers Qwen2.5-0.5B-Instruct; do
  if [ -d "${MODELS_DIR}/${optional}" ] || [ -f "${MODELS_DIR}/${optional}" ]; then
    cp -a "${MODELS_DIR}/${optional}" "${STAGE}/models/"
    info "已打包 ${optional}"
  fi
done
if [ -d "${MODELS_DIR}/argos" ]; then
  cp -a "${MODELS_DIR}/argos" "${STAGE}/models/"
  info "已打包 argos 语言包: ${MODELS_DIR}/argos"
else
  warn "未找到 ${MODELS_DIR}/argos —— 翻译功能需要的 argos 语言包未包含，请自行下载后放入包内 models/argos/"
fi

# ---------- Step 4: 前端地图资产（存在则打包，不存在则警告注明） ----------
MAP_FOUND=0
for base in "${REPO_ROOT}/frontend/public" "${REPO_ROOT}/frontend/dist"; do
  [ -d "${base}" ] || continue
  while IFS= read -r -d '' d; do
    rel="${d#"${REPO_ROOT}/frontend/"}"
    mkdir -p "${STAGE}/maps/$(dirname "${rel}")"
    cp -a "${d}" "${STAGE}/maps/${rel}"
    info "已打包地图资产: frontend/${rel}"
    MAP_FOUND=1
  done < <(find "${base}" -maxdepth 3 -type d \( -iname '*map*' -o -iname '*tile*' -o -iname '*geo*' \) -print0 2>/dev/null)
done
if [ "${MAP_FOUND}" = "0" ]; then
  rmdir "${STAGE}/maps" 2>/dev/null || true
  warn "frontend/public 与 frontend/dist 中未发现地图/tiles 资产目录 —— 如看板使用离线地图，请自行将资产放入包内 maps/ 并在前端配置指向"
fi

# ---------- Step 5: 打包源码（tar --exclude 排除构建缓存/本地状态） ----------
info "打包 deploy/ scripts/ backend/ frontend/ 源码..."
tar -C "${REPO_ROOT}" \
  --exclude='backend/.env' --exclude='backend/.env.*' \
  --exclude='backend/__pycache__' --exclude='backend/**/__pycache__' \
  --exclude='backend/.mypy_cache' --exclude='backend/.pytest_cache' \
  -cf - backend | tar -xf - -C "${STAGE}"
tar -C "${REPO_ROOT}" \
  --exclude='frontend/node_modules' --exclude='frontend/dist' \
  --exclude='frontend/.vite' --exclude='frontend/.omc' \
  -cf - frontend | tar -xf - -C "${STAGE}"
tar -C "${REPO_ROOT}" -cf - deploy scripts | tar -xf - -C "${STAGE}"
cp "${REPO_ROOT}/scripts/install.sh" "${STAGE}/install.sh"
chmod +x "${STAGE}/install.sh" "${STAGE}/scripts/"*.sh 2>/dev/null || true

# ---------- Step 6: 生成全包 SHA256SUMS ----------
info "生成全包 SHA256SUMS 清单..."
(cd "${STAGE}" && find . -type f ! -name 'SHA256SUMS' -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)

# ---------- Step 7: 整包压缩归档（便于一次性拷贝到离线环境） ----------
ARCHIVE="${OUTPUT_DIR}/${PKG_NAME}-$(date +%Y%m%d).tar.gz"
info "生成压缩归档: ${ARCHIVE}"
tar -C "${OUTPUT_DIR}" -czf "${ARCHIVE}" "${PKG_NAME}" || die "压缩归档失败"
sha256sum "${ARCHIVE}" > "${ARCHIVE}.sha256"

# ---------- 完成报告 ----------
echo ""
echo "========================================"
echo "  离线安装包构建完成"
echo "  目录: ${STAGE}"
echo "  归档: ${ARCHIVE} ($(du -h "${ARCHIVE}" | cut -f1))"
echo "  包总大小: $(du -sh "${STAGE}" | cut -f1)"
echo "========================================"
echo ""
echo "内容清单:"
(cd "${STAGE}" && find . -maxdepth 2 -mindepth 1 | sort | sed 's/^/  /')
echo ""
echo "拷贝到离线环境后：解压归档 → 进入 ${PKG_NAME}/ → bash install.sh"
echo "（install.sh 将自动识别离线模式：校验 images/SHA256SUMS → docker load → 启动全栈）"
