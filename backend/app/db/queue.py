"""Redis Streams 队列封装（生产者/消费者组/ACK/死信），供 collector→extractor→nlp_pipeline 解耦。"""
import json
from typing import Any

import redis

STREAM_RAW_ARTICLES = "raw:articles"
STREAM_DLQ_SUFFIX = ":dlq"


class StreamQueue:
    def __init__(self, client: redis.Redis):
        self.client = client

    def publish(self, stream: str, payload: dict[str, Any], trace_id: str = "") -> str:
        fields = {"trace_id": trace_id, "data": json.dumps(payload, ensure_ascii=False)}
        msg_id = self.client.xadd(stream, fields)  # type: ignore[arg-type]
        return str(msg_id)

    def ensure_group(self, stream: str, group: str) -> None:
        try:
            self.client.xgroup_create(stream, group, id="0", mkstream=True)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def consume(self, stream: str, group: str, consumer: str, count: int = 10, block_ms: int = 5000):
        self.ensure_group(stream, group)
        resp: Any = self.client.xreadgroup(group, consumer, {stream: ">"}, count=count, block=block_ms)
        if not resp:
            return []
        first = resp[0]  # [(stream, entries), ...] 单流读取只取第一个
        entries = first[1] if isinstance(first, (list, tuple)) else next(iter(first.values()))
        return entries or []

    def ack(self, stream: str, group: str, msg_id: str) -> None:
        self.client.xack(stream, group, msg_id)

    def to_dlq(self, stream: str, msg_id: str, fields: dict, reason: str) -> None:
        self.client.xadd(stream + STREAM_DLQ_SUFFIX, {"dead_msg_id": msg_id, "reason": reason, **fields})

    def pending_count(self, stream: str, group: str) -> int:
        try:
            return self.client.xpending(stream, group)["pending"]
        except redis.exceptions.ResponseError:
            return 0

    def length(self, stream: str) -> int:
        return self.client.xlen(stream)
