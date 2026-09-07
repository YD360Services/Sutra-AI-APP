"""
Redis-optional cache layer for CopilotX.

- If REDIS_URL points to a live Redis instance, all state is stored there
  with TTL expiry (cross-process, survives restarts).
- If Redis is unavailable (Railway without a Redis addon, local dev without
  Redis running, etc.) the layer falls back to a bounded in-process LRU store
  with per-entry TTL. The app works identically — zero Redis dependency at
  runtime.

Public API is identical to the previous version; all callers are unaffected.
"""

import json
import logging
import time
import threading
from collections import OrderedDict
from typing import Any, Dict, Optional

from app.core.config import settings

logger = logging.getLogger("copilotx.redis")


# ---------------------------------------------------------------------------
# Bounded in-memory LRU store with per-entry TTL
# ---------------------------------------------------------------------------
class _TTLLRUCache:
    """
    Thread-safe LRU dict with per-entry TTL expiry.
    maxsize: maximum number of entries — oldest evicted when exceeded.
    Entries expire silently on read once their TTL has elapsed.
    """

    def __init__(self, maxsize: int = 512):
        self._maxsize = maxsize
        self._store: "OrderedDict[str, tuple[Any, float]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key not in self._store:
                return None
            value, expires_at = self._store[key]
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            self._store.move_to_end(key)
            return value

    def set(self, key: str, value: Any, ttl_seconds: int = 14400):
        with self._lock:
            expires_at = time.monotonic() + ttl_seconds
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (value, expires_at)
            if len(self._store) > self._maxsize:
                self._store.popitem(last=False)

    def delete(self, key: str):
        with self._lock:
            self._store.pop(key, None)


# ---------------------------------------------------------------------------
# Main cache class
# ---------------------------------------------------------------------------
class RedisCache:
    def __init__(self):
        self.redis_url = settings.REDIS_URL
        self._client = None                            # redis.asyncio.Redis | None
        self._local_cache = _TTLLRUCache(maxsize=512)  # bounded TTL LRU fallback
        self._redis_available = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def connect(self):
        """
        Try to connect to Redis. Falls back silently to in-memory mode
        when Redis is unavailable. Uses a 1-second timeout for localhost
        to avoid blocking startup.
        """
        connect_timeout = 1 if "localhost" in (self.redis_url or "") else 3
        try:
            import redis.asyncio as _redis
            client = _redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=connect_timeout,
                socket_timeout=connect_timeout,
            )
            await client.ping()
            self._client = client
            self._redis_available = True
            logger.info(f"Connected to Redis at {self.redis_url} successfully.")
        except Exception as e:
            logger.info(
                "Redis not available — running in in-memory fallback mode "
                "(fully functional; session state is process-local). "
                f"Reason: {e}"
            )
            self._client = None
            self._redis_available = False

    async def disconnect(self):
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internal read/write helpers
    # ------------------------------------------------------------------
    async def _get(self, key: str) -> Optional[str]:
        """Read from Redis first; fall back to local LRU."""
        if self._client:
            try:
                val = await self._client.get(key)
                if val is not None:
                    return val
            except Exception as e:
                logger.debug(f"Redis GET error for {key}: {e}")
        return self._local_cache.get(key)

    async def _set(self, key: str, value: str, ttl: int = 14400):
        """Write to local LRU always; also push to Redis when available."""
        self._local_cache.set(key, value, ttl_seconds=ttl)
        if self._client:
            try:
                await self._client.setex(key, ttl, value)
            except Exception as e:
                logger.debug(f"Redis SET error for {key}: {e}")

    async def _del(self, key: str):
        self._local_cache.delete(key)
        if self._client:
            try:
                await self._client.delete(key)
            except Exception as e:
                logger.debug(f"Redis DEL error for {key}: {e}")

    # ------------------------------------------------------------------
    # Session state
    # ------------------------------------------------------------------
    async def get_session_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        raw = self._local_cache.get(f"session:{session_id}")
        if raw is not None:
            return raw if isinstance(raw, dict) else json.loads(raw)
        if self._client:
            try:
                data = await self._client.get(f"session:{session_id}")
                if data:
                    parsed = json.loads(data)
                    # Populate local cache for next hit
                    self._local_cache.set(f"session:{session_id}", parsed)
                    return parsed
            except Exception as e:
                logger.debug(f"Redis GET session error: {e}")
        return None

    async def set_session_state(
        self, session_id: str, state: Dict[str, Any], expire_seconds: int = 14400
    ):
        # Store dict directly in local cache (avoids double serialize/parse)
        self._local_cache.set(f"session:{session_id}", state, ttl_seconds=expire_seconds)
        if self._client:
            try:
                await self._client.setex(
                    f"session:{session_id}", expire_seconds, json.dumps(state)
                )
            except Exception as e:
                logger.debug(f"Redis SET session error: {e}")

    # ------------------------------------------------------------------
    # Transcript
    # ------------------------------------------------------------------
    async def get_transcript(self, session_id: str) -> Optional[str]:
        return await self._get(f"transcript:{session_id}")

    async def set_transcript(
        self, session_id: str, transcript: str, expire_seconds: int = 14400
    ):
        await self._set(f"transcript:{session_id}", transcript, expire_seconds)

    # ------------------------------------------------------------------
    # Prepared prompt
    # ------------------------------------------------------------------
    async def get_prepared_prompt(self, session_id: str) -> Optional[str]:
        return await self.get_cached_item(f"prepared_prompt:{session_id}")

    async def set_prepared_prompt(
        self, session_id: str, prompt: str, expire_seconds: int = 14400
    ):
        await self.set_cached_item(f"prepared_prompt:{session_id}", prompt, expire_seconds)

    # ------------------------------------------------------------------
    # Resume
    # ------------------------------------------------------------------
    async def get_resume(self, resume_id: str) -> Optional[str]:
        return await self.get_cached_item(f"resume:{resume_id}")

    async def set_resume(
        self, resume_id: str, content: str, expire_seconds: int = 14400
    ):
        await self.set_cached_item(f"resume:{resume_id}", content, expire_seconds)

    # ------------------------------------------------------------------
    # Generic key/value
    # ------------------------------------------------------------------
    async def get_cached_item(self, key: str) -> Optional[str]:
        return await self._get(key)

    async def set_cached_item(
        self, key: str, content: str, expire_seconds: int = 14400
    ):
        await self._set(key, content, expire_seconds)

    async def delete_cached_item(self, key: str):
        await self._del(key)

    # ------------------------------------------------------------------
    # WebSocket tracking (best-effort; silent no-op without Redis)
    # ------------------------------------------------------------------
    async def track_websocket(
        self, session_id: str, client_id: str, register: bool = True
    ):
        if not self._client:
            return
        key = f"ws:{session_id}"
        try:
            if register:
                await self._client.sadd(key, client_id)
                await self._client.expire(key, 14400)
            else:
                await self._client.srem(key, client_id)
        except Exception as e:
            logger.debug(f"Redis WS tracking error: {e}")

    async def count_active_websockets(self, session_id: str) -> int:
        if not self._client:
            return 0
        try:
            return await self._client.scard(f"ws:{session_id}")
        except Exception:
            return 0


redis_cache = RedisCache()



