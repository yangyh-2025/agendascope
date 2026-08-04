# AgendaScope 本地算力机 worker

本目录包含本地算力机的 docker compose 编排文件。所有 worker 通过公网 PostgreSQL 连接云端数据库（39.106.2.28:5432），云端只保留 PG + FastAPI + nginx 三个服务。

## 前置条件

- **硬件**：8 核 CPU + 32GB 内存 + 512GB NVMe SSD（方案 A，无 GPU）
- **操作系统**：Ubuntu 22.04 LTS / Windows 11 + WSL2
- **网络**：100Mbps 带宽 + 能访问境外（采集 408 个媒体源需要 mihomo 代理）
- **软件**：Docker 24+ 和 docker compose v2
- **模型**：下载 `bge-m3` 到 `./models/`（约 5GB）
  ```bash
  mkdir -p models
  # 从 HuggingFace 下载（需代理）
  huggingface-cli download BAAI/bge-m3 --local-dir models/bge-m3
  ```

## 初次部署

### 1. 服务器侧（云端）一次性配置

```bash
# SSH 到云服务器
ssh root@39.106.2.28

# 生成 PG SSL 证书
cd /root/agendascope
bash deploy/postgresql/generate_ssl_cert.sh

# 创建 PG 公网账号（改 CHANGE_ME 为强密码）
docker exec -i agendascope-db-1 psql -U agenda -d agendascope < deploy/postgresql/init_public_users.sql

# 把 ca.crt 复制到本地（用 scp 或 U 盘）
scp root@39.106.2.28:/root/agendascope/deploy/postgresql/ssl/ca.crt ./ssl/ca.crt

# 配置宝塔防火墙：5432 端口只放行本地算力机的公网 IP
```

### 2. 本地算力机配置

```bash
# 克隆仓库
git clone https://github.com/yangyh-2025/agendascope.git
cd agendascope/local_workers

# 配置环境变量
cp .env.example .env
vim .env
# 改 DATABASE_URL 的 CHANGE_ME 为 init_public_users.sql 里设置的强密码
# 改 LLM_API_KEY 为智谱 GLM-4 API Key

# 同步后端镜像（云端和本地用同一份代码）
# 方法 1：从云端 pull
scp root@39.106.2.28:/root/agendascope-backend-latest.tar.gz .
docker load -i agendascope-backend-latest.tar.gz
# 方法 2：本地构建
cd ../backend
docker build -t agendascope-backend:latest -f ../deploy/Dockerfile.backend .
cd ../local_workers

# 创建 SSL 目录并放入 ca.crt
mkdir -p ssl
cp /path/to/ca.crt ssl/ca.crt

# 启动所有 worker
docker compose --env-file .env up -d

# 查看日志
docker compose --env-file .env logs -f collector
docker compose --env-file .env logs -f nlp-worker
```

### 3. 验证

```bash
# 检查所有 worker 状态
docker compose --env-file .env ps

# 检查云端数据库连接
docker exec -it local_workers-collector-1 python -c "
from app.db.session import get_session_factory
db = get_session_factory()()
from sqlalchemy import text
print(db.scalar(text('SELECT count(*) FROM sources')))
"
# 应输出 408（媒体源数量）
```

## 日常运维

```bash
# 停止所有 worker
docker compose --env-file .env down

# 重启某个 worker
docker compose --env-file .env restart collector

# 查看资源占用
docker stats

# 更新代码后重建镜像
cd ../backend && docker build -t agendascope-backend:latest -f ../deploy/Dockerfile.backend .
cd ../local_workers && docker compose --env-file .env up -d --force-recreate
```

## 故障排查

- **PG 连接超时**：检查云服务器防火墙 5432 端口是否放行本地 IP；`telnet 39.106.2.28 5432`
- **SSL 证书错误**：检查 `ssl/ca.crt` 是否与服务器一致；改 `sslmode=require` 为 `sslmode=disable` 临时调试
- **采集失败**：检查 `GLOBAL_SITE_PROXY` 是否指向本地 mihomo；`curl -x http://localhost:7890 https://www.google.com`
- **内存爆**：`docker stats` 看哪个 worker 超了；降 `COLLECT_MAX_WORKERS` 或 `NLP_WORKER_BATCH_SIZE`

## 分布式扩展

如需多台机器分担负载，每台跑 `docker-compose.yml` 的子集即可。v3.0 的 `article_processing` 状态机 + `FOR UPDATE SKIP LOCKED` 保证多机并发安全：

- **采集机**：只跑 `collector`
- **计算机**：跑 `nlp-worker` + `cluster-worker` + `entity-worker`
- **LLM 机**：跑 `relation-worker` + `naming-worker`

示例（计算机）：
```bash
docker compose --env-file .env up -d nlp-worker cluster-worker entity-worker
```
