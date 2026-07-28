# 备份恢复演练 Runbook（Phase 5 T5.11）

适用范围：AgendaScope 观澜私有化单机部署（deploy/docker-compose.yml，项目名 agendascope）。
配套脚本：`scripts/backup.sh`（备份）、`scripts/restore.sh`（恢复）。

## 1. 演练频率与责任人

| 项目 | 约定 |
| --- | --- |
| 频率 | 每月一次，安排在业务低峰窗口（建议每月第一个周末） |
| 责任人 | 运维负责人（执行）+ 后端负责人（复核校验结果） |
| 备份策略 | 每日全量（cron 调用 `BACKUP_MODE=full`），日间每 4 小时增量（`BACKUP_MODE=incremental`），保留 30 天 |
| 告警 | 备份连续失败 ≥2 次时 backup.sh 自动向 `alerts` 表写入 P1 告警（系统规则「系统备份失败告警」），值班人需在系统内处理并归档 |

## 2. 前置检查

1. **备份产物完整性**：每份文件产物带 `.sha256` 校验文件，执行：

   ```bash
   cd "$BACKUP_DIR" && sha256sum -c agendascope-<ts>.sql.gz.enc.sha256
   ```

   全部产物（全量、增量包、Redis 包）逐一校验，任一失败则该备份点不可用于演练，改选上一备份点并排查备份链路。

2. **密钥可用性**：`BACKUP_KEY_FILE` 指向的密钥文件存在且可解密：

   ```bash
   openssl enc -d -aes-256-cbc -pbkdf2 -pass file:"$BACKUP_KEY_FILE" \
     -in "$BACKUP_DIR/agendascope-<ts>.sql.gz.enc" | head -c 100 | gunzip -c | head -n1
   ```

   能输出 SQL 头（如 `-- PostgreSQL database dump`）即密钥正确。密钥由部署方离线保管（生成：`openssl rand -base64 32 > <密钥文件>`），丢失则全部加密备份不可恢复。

3. **ES 快照仓库**：`curl -sf http://localhost:9200/_snapshot/backup` 返回已注册仓库；目标快照存在且 `state=SUCCESS`（`curl -sf http://localhost:9200/_snapshot/backup/snapshot_<ts>`）。
   部署要求：compose 由其他负责人维护，部署侧必须为 elasticsearch 服务配置 `path.repo: /usr/share/elasticsearch/backups` 并挂载宿主机目录到该路径（详见 backup.sh 头部注释）。

4. **磁盘余量**：备份目录所在盘与 Docker 数据盘剩余空间 ≥ 当前 PG dump 明文体积的 3 倍。

5. **演练通告**：提前通知使用方，演练期间系统只读不可用约 30 分钟。

## 3. 恢复演练分步操作

执行入口（推荐用时间戳，脚本自动接续该点之后的全部增量）：

```bash
export BACKUP_KEY_FILE=/secure/path/backup.key
bash scripts/restore.sh <YYYYMMDD-HHMMSS>
```

脚本内部步骤（对照排障用）：

1. **停写**：`docker compose stop backend worker nlp-worker cluster-worker naming-worker agenda-worker snapshot-worker`，逐个确认容器已停。
2. **PG 恢复**：`DROP SCHEMA public CASCADE; CREATE SCHEMA public;` 后灌入全量 dump（`.enc` 先解密），再按时间戳升序对该点之后的每个增量包逐表 `COPY FROM STDIN`（表顺序：articles → topics → topic_articles → agenda_events → agenda_event_evidence → agenda_snapshots → alerts → alert_rules）。**顺序不可颠倒**：增量依赖全量基线，增量之间按时间衔接，乱序或缺段会产生主键冲突或数据空洞。
3. **ES 恢复**：注册快照仓库 → 校验快照 `state=SUCCESS` → close 索引 → `_restore?wait_for_completion=true` → open 索引 → `_cat/indices` 校验文档数非 0。
4. **Redis 恢复**：stop redis → 清空数据卷旧 RDB/AOF（AOF 开启时启动优先读 AOF，不清除则 RDB 恢复无效）→ docker cp 回 dump.rdb 与 appendonlydir → start redis → PING / DBSIZE 校验。
5. **校验**：PG 核心表（articles/topics/agenda_events）行数非 0；后端 `http://localhost:8000/health` 返回 200（注意：健康路由挂在根路径 `/health`，不在 `/api/v1` 前缀下）。
6. **恢复服务**：校验全部通过后 start backend（健康检查前置已做）与其余 6 个写入方服务。

## 4. RTO ≤30 min 验证清单

演练时分段掐表（restore.sh 结束时自动输出下表，誊抄到演练记录）：

| 阶段 | 目标参考 | 实际耗时 |
| --- | --- | --- |
| 停写（7 个写入方服务） | ≤2 min | |
| PG 恢复（全量+增量） | ≤15 min | |
| ES 恢复 | ≤5 min | |
| Redis 恢复 | ≤2 min | |
| 校验（PG 行数/ES 文档数/health） | ≤3 min | |
| 恢复服务 | ≤2 min | |
| **总计** | **≤30 min** | |

判定标准：总耗时 ≤1800 s 且三项校验（PG 行数非 0、ES 文档数非 0、/health 200）全部通过，演练记为成功；超时或校验失败记为失败并启动根因分析。

## 5. 演练结果记录模板

```text
演练日期：
执行人 / 复核人：
备份点（全量时间戳）：
应用增量数量与区间：
产物 sha256 校验：通过 / 不通过（说明）
分段耗时：停写 __s / PG __s / ES __s / Redis __s / 校验 __s / 恢复服务 __s / 总计 __s
RTO 达标：是 / 否
校验结果：PG 行数 articles=__ topics=__ agenda_events=__；ES docs=__；/health=__
数据抽样核对（抽 3 篇文章标题/1 个议题与备份前记录比对）：
异常与处置：
结论与改进项：
```

## 6. 常见故障处置

| 故障 | 现象 | 处置 |
| --- | --- | --- |
| 解密失败 | `openssl ... bad decrypt` | 确认 `BACKUP_KEY_FILE` 与备份时同一把密钥；用前置检查第 2 条验证密钥；密钥轮换后用旧密钥解旧备份。仍失败则该产物损坏，改用上备份点并排查存储。 |
| 快照仓库未注册 | backup/restore 报 `repository missing` 或注册 PUT 失败 | ES 侧未配置 `path.repo`。compose 由其他负责人维护，提需求：elasticsearch 服务加 `environment: path.repo: /usr/share/elasticsearch/backups` 并挂载宿主机目录到该路径，重启 ES 后重试。 |
| 磁盘不足 | pg_dump/解压/COPY 中途写失败，备份目录或 Docker 盘满 | `df -h` 确认；手工清理超期产物（`find "$BACKUP_DIR" -mtime +30 -delete` 前先确认无演练依赖）；恢复前确保目标盘余量 ≥ dump 明文 3 倍。 |
| 增量 COPY 主键冲突 | restore.sh 在增量应用阶段报错退出 | 多为增量缺段/乱序或基线选错。确认所选全量时间戳之后增量链完整（`ls "$BACKUP_DIR"/agendascope-inc-*` 对照 `.last_backup_ts` 区间）；必要时退回上一个全量点重放完整增量链。 |
| BGSAVE 超时 | backup.sh 报「120s 内未落盘」 | 检查 Redis 容器内存与磁盘 IO（`docker logs agendascope-redis-1`）；大内存实例改低峰备份。 |
| 告警写入失败 | backup.sh 日志「跳过 P1 告警写入」 | 多为 DB 同时故障（此时备份失败与告警失败同根因）。先恢复 DB，再手工确认 `alerts` 表无遗漏并补录事件。 |
| /health 不通过 | restore.sh 校验阶段退出 | `curl -s localhost:8000/health` 看 components 哪个为 false，对应检查该组件容器日志；写入方保持停止状态，修复后重新执行校验与恢复服务步骤。 |
