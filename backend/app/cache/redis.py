"""
Pure in-memory LRU cache layer for RoundMate AI.

Provides high-performance, thread-safe in-process caching with per-entry TTL expiry.
Fully decouples the architecture from external Redis instances and external service dependencies.
Maintains 100% backward-compatible API so all callers remain unaffected.
"""

import json
import logging
import time
import threading
from collections import OrderedDict
from typing import Any, Dict, Optional, Set

logger = logging.getLogger("roundmate.cache")


# ---------------------------------------------------------------------------
# Bounded in-memory LRU store with per-entry TTL
# ---------------------------------------------------------------------------
class _TTLLRUCache:
    """
    Thread-safe LRU dictionary with per-entry TTL expiry.
    maxsize: maximum number of entries — oldest evicted when exceeded.
    Entries expire silently on read once their TTL has elapsed.
    """

    def __init__(self, maxsize: int = 1024):
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
# Unified Cache Class (Maintains RedisCache interface for zero breakage)
# ---------------------------------------------------------------------------
class RedisCache:
    def __init__(self):
        self._local_cache = _TTLLRUCache(maxsize=1024)
        self._ws_clients: Dict[str, Set[str]] = {}
        self._ws_lock = threading.Lock()
        self._client = None
        self._redis_available = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def connect(self):
        """Active in-memory high-speed caching."""
        logger.info("In-memory cache active (zero external Redis dependency).")

    async def disconnect(self):
        """Clean shutdown hook."""
        pass

    # ------------------------------------------------------------------
    # Internal read/write helpers
    # ------------------------------------------------------------------
    async def _get(self, key: str) -> Optional[str]:
        return self._local_cache.get(key)

    async def _set(self, key: str, value: str, ttl: int = 14400):
        self._local_cache.set(key, value, ttl_seconds=ttl)

    async def _del(self, key: str):
        self._local_cache.delete(key)

    # ------------------------------------------------------------------
    # Session state
    # ------------------------------------------------------------------
    async def get_session_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        raw = self._local_cache.get(f"session:{session_id}")
        if raw is not None:
            return raw if isinstance(raw, dict) else json.loads(raw)
        return None

    async def set_session_state(
        self, session_id: str, state: Dict[str, Any], expire_seconds: int = 14400
    ):
        self._local_cache.set(f"session:{session_id}", state, ttl_seconds=expire_seconds)

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
    # WebSocket tracking (thread-safe in-memory)
    # ------------------------------------------------------------------
    async def track_websocket(
        self, session_id: str, client_id: str, register: bool = True
    ):
        with self._ws_lock:
            key = f"ws:{session_id}"
            if key not in self._ws_clients:
                self._ws_clients[key] = set()
            if register:
                self._ws_clients[key].add(client_id)
            else:
                self._ws_clients[key].discard(client_id)

    async def count_active_websockets(self, session_id: str) -> int:
        with self._ws_lock:
            key = f"ws:{session_id}"
            return len(self._ws_clients.get(key, set()))


redis_cache = RedisCache()
