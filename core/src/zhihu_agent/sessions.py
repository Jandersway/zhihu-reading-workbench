from __future__ import annotations

import json
import re
from typing import Any


class RedisSessions:
    """Small, expiring chat history; no article text or credentials are stored."""

    def __init__(self, url: str, ttl_seconds: int = 3600):
        try:
            import redis
            from redis.exceptions import RedisError
        except ImportError as exc:
            raise RuntimeError("Install the redis extra to use REDIS_URL") from exc
        self.client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=3)
        self._redis_error = RedisError
        self.ttl_seconds = ttl_seconds

    def ping(self) -> None:
        try:
            self.client.ping()
        except self._redis_error:
            raise RuntimeError("Redis session storage is unavailable") from None

    @staticmethod
    def _key(session_id: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", session_id):
            raise ValueError("Invalid session ID")
        return f"zhihu-agent:session:{session_id}"

    def load(self, session_id: str) -> list[dict[str, str]]:
        try:
            records = self.client.lrange(self._key(session_id), -6, -1)
        except self._redis_error:
            raise RuntimeError("Redis session storage is unavailable") from None
        return [json.loads(record) for record in records]

    def append_turn(self, session_id: str, question: str, answer: str) -> None:
        key = self._key(session_id)
        items: list[dict[str, Any]] = [
            {"role": "user", "content": question[:4000]},
            {"role": "assistant", "content": answer[:4000]},
        ]
        try:
            with self.client.pipeline() as pipe:
                pipe.rpush(key, *(json.dumps(item, ensure_ascii=False) for item in items))
                pipe.ltrim(key, -6, -1)
                pipe.expire(key, self.ttl_seconds)
                pipe.execute()
        except self._redis_error:
            raise RuntimeError("Redis session storage is unavailable") from None
