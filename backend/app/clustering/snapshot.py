"""重聚类校正快照发布（T2.9）：Redis L2 快照，读写不一致规避。

写侧（重聚类）：开始置 status=correcting → 计算与落库 → 原子替换 latest 快照 → status=ready。
读侧（看板/前端）：始终读上一版 latest 快照；status=correcting 时照常返回旧版并带
correcting=true 标注"校正中"——校正期间绝不阻塞读、绝不读到半更新状态
（详细设计缓存层：`cluster:snapshot:latest`，校正完成替换，读旧版不阻塞）。
"""
import json
from datetime import UTC, datetime
from typing import Any

import redis as redis_lib

from app.clustering.config import get_cluster_settings

STATUS_READY = "ready"
STATUS_CORRECTING = "correcting"


def mark_correcting(redis_client: redis_lib.Redis) -> None:
    settings = get_cluster_settings()
    redis_client.setex(settings.snapshot_status_key, settings.snapshot_ttl_seconds, STATUS_CORRECTING)


def mark_ready(redis_client: redis_lib.Redis) -> None:
    settings = get_cluster_settings()
    redis_client.setex(settings.snapshot_status_key, settings.snapshot_ttl_seconds, STATUS_READY)


def publish_snapshot(redis_client: redis_lib.Redis, snapshot: dict[str, Any]) -> None:
    """原子替换最新快照（单键 SET 天然原子），版本号取发布时刻 ISO 时间戳。"""
    settings = get_cluster_settings()
    payload = {
        "version": datetime.now(UTC).isoformat(),
        "correcting": False,
        **snapshot,
    }
    redis_client.setex(settings.snapshot_key, settings.snapshot_ttl_seconds, json.dumps(payload, ensure_ascii=False))


def read_snapshot(redis_client: redis_lib.Redis) -> dict[str, Any]:
    """读侧入口：返回 {status, correcting, snapshot}；快照缺失时 snapshot=None。"""
    settings = get_cluster_settings()
    status = str(redis_client.get(settings.snapshot_status_key) or STATUS_READY)
    raw = redis_client.get(settings.snapshot_key)
    snapshot = json.loads(str(raw)) if raw else None
    if snapshot is not None and status == STATUS_CORRECTING:
        snapshot = {**snapshot, "correcting": True}  # 旧版快照照常可读，标注"校正中"
    return {"status": status, "correcting": status == STATUS_CORRECTING, "snapshot": snapshot}
