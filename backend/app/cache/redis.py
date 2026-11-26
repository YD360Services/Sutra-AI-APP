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
    # Lifecycle (Render Key Value / Valkey with local LRU fallback)
    # ------------------------------------------------------------------
    async def connect(self):
        """Connects to Render Key Value (Valkey) if REDIS_URL is set, else uses in-memory LRU."""
        import os
        redis_url = os.getenv("REDIS_URL")
        if redis_url:
            try:
                import redis.asyncio as aioredis
                self._client = aioredis.from_url(redis_url, decode_responses=True)
                await self._client.ping()
                self._redis_available = True
                logger.info("Connected to Valkey / Redis instance via REDIS_URL.")
                return
            except Exception as e:
                logger.warning(f"Valkey connection failed ({e}). Falling back to in-memory LRU cache.")
                self._client = None
                self._redis_available = False
        logger.info("In-memory LRU cache active (local dev / test mode).")

    async def disconnect(self):
        """Clean shutdown hook."""
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
            self._redis_available = False

    # ------------------------------------------------------------------
    # Internal read/write helpers (Valkey atomic or in-memory fallback)
    # ------------------------------------------------------------------
    async def _get(self, key: str) -> Optional[str]:
        if self._redis_available and self._client:
            try:
                return await self._client.get(key)
            except Exception as e:
                logger.debug(f"Valkey _get error ({key}): {e}")
        return self._local_cache.get(key)

    async def _set(self, key: str, value: str, ttl: int = 14400):
        if self._redis_available and self._client:
            try:
                await self._client.set(key, value, ex=ttl)
                return
            except Exception as e:
                logger.debug(f"Valkey _set error ({key}): {e}")
        self._local_cache.set(key, value, ttl_seconds=ttl)

    async def _incr(self, key: str, ttl: int = 86400) -> int:
        if self._redis_available and self._client:
            try:
                val = await self._client.incr(key)
                await self._client.expire(key, ttl)
                return int(val)
            except Exception as e:
                logger.debug(f"Valkey _incr error ({key}): {e}")
        current = self._local_cache.get(key)
        new_val = (int(current) if current is not None else 1) + 1
        self._local_cache.set(key, str(new_val), ttl_seconds=ttl)
        return new_val

    async def _del(self, key: str):
        if self._redis_available and self._client:
            try:
                await self._client.delete(key)
                return
            except Exception as e:
                logger.debug(f"Valkey _del error ({key}): {e}")
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
    # Predictive Question & RAG Cache
    # ------------------------------------------------------------------
    async def get_predictive_cache(self, session_id: str) -> Optional[Dict[str, Any]]:
        raw = self._local_cache.get(f"predictive:{session_id}")
        if raw is not None:
            return raw if isinstance(raw, dict) else json.loads(raw)
        return None

    async def set_predictive_cache(
        self, session_id: str, data: Dict[str, Any], expire_seconds: int = 120
    ):
        self._local_cache.set(f"predictive:{session_id}", data, ttl_seconds=expire_seconds)

    # ------------------------------------------------------------------
    # Versioned & Tenant-Scoped Supporting Evidence Cache (LLD v1.3 Item 38)
    # ------------------------------------------------------------------
    async def set_versioned_evidence(
        self,
        tenant_id: str,
        key: str,
        data: Any,
        source_version: str,
        context_fingerprint: str,
        ttl_seconds: int = 300,
    ) -> None:
        scoped_key = f"rm:tenant:{tenant_id}:evidence:{key}"
        envelope = {
            "data": data,
            "source_version": source_version,
            "context_fingerprint": context_fingerprint,
            "created_at": time.time(),
        }
        await self._set(scoped_key, json.dumps(envelope), ttl=ttl_seconds)

    async def get_versioned_evidence(
        self,
        tenant_id: str,
        key: str,
        expected_source_version: str,
        expected_fingerprint: str,
    ) -> Optional[Any]:
        """Rejects stale or fingerprint-mismatched evidence."""
        scoped_key = f"rm:tenant:{tenant_id}:evidence:{key}"
        raw = await self._get(scoped_key)
        if not raw:
            return None
        try:
            env = json.loads(raw)
            if env.get("source_version") != expected_source_version:
                return None
            if env.get("context_fingerprint") != expected_fingerprint:
                return None
            return env.get("data")
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Distributed Generation & Question Version Guard (LLD v1.3 Valkey)
    # ------------------------------------------------------------------
    async def get_generation_version(self, session_id: str) -> int:
        val = await self._get(f"gen_ver:{session_id}")
        return int(val) if val is not None else 1

    async def increment_generation_version(self, session_id: str) -> int:
        current = await self.get_generation_version(session_id)
        new_val = current + 1
        await self._set(f"gen_ver:{session_id}", str(new_val), ttl=86400)
        return new_val

    async def get_question_version(self, session_id: str) -> int:
        val = await self._get(f"q_ver:{session_id}")
        return int(val) if val is not None else 1

    async def increment_question_version(self, session_id: str) -> int:
        current = await self.get_question_version(session_id)
        new_val = current + 1
        await self._set(f"q_ver:{session_id}", str(new_val), ttl=86400)
        return new_val

    async def cancel_generation(self, session_id: str, generation_id: Optional[str] = None) -> int:
        """
        Increments the session's generation_version to invalidate in-flight generators.
        Optionally sets a direct cancellation flag for generation_id.
        """
        new_gen_ver = await self.increment_generation_version(session_id)
        if generation_id:
            await self._set(f"cancelled_gen:{generation_id}", "1", ttl=300)
        return new_gen_ver

    async def is_generation_cancelled(self, generation_id: str) -> bool:
        flag = await self._get(f"cancelled_gen:{generation_id}")
        return flag == "1"

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

class ValkeyKeyFactory:
    """Enforces LLD v1.3 hierarchical key naming across distributed workers."""

    @staticmethod
    def question_version(session_id: str, question_id: str = "default") -> str:
        return f"rm:session:{session_id}:question:{question_id}:version"

    @staticmethod
    def generation_version(session_id: str, question_id: str = "default") -> str:
        return f"rm:session:{session_id}:question:{question_id}:generation_version"

    @staticmethod
    def active_generation(session_id: str, question_id: str = "default") -> str:
        return f"rm:session:{session_id}:question:{question_id}:active_generation"

    @staticmethod
    def generation_cancelled(generation_id: str) -> str:
        return f"rm:generation:{generation_id}:cancelled"

    @staticmethod
    def ws_ticket(ticket: str) -> str:
        return f"rm:ws_ticket:{ticket}"


class ValkeySessionCoordinator:
    """
    LLD v1.3 State Coordinator for Render Key Value (Valkey) and in-memory caches.
    Ensures monotonic question and generation lifecycles across distributed workers.
    """
    @staticmethod
    async def get_state(session_id: str, question_id: str = "default"):
        q_key = ValkeyKeyFactory.question_version(session_id, question_id)
        g_key = ValkeyKeyFactory.generation_version(session_id, question_id)
        q_v = await redis_cache._get(q_key)
        g_v = await redis_cache._get(g_key)
        return (int(q_v) if q_v is not None else 1, int(g_v) if g_v is not None else 1)

    @staticmethod
    async def bump_question_version(session_id: str, question_id: str = "default"):
        q_key = ValkeyKeyFactory.question_version(session_id, question_id)
        g_key = ValkeyKeyFactory.generation_version(session_id, question_id)
        q_v = await redis_cache._incr(q_key, ttl=86400)
        g_v = await redis_cache._incr(g_key, ttl=86400)
        return (q_v, g_v)

    @staticmethod
    async def set_active_generation(session_id: str, question_id: str, generation_id: str):
        key = ValkeyKeyFactory.active_generation(session_id, question_id)
        await redis_cache._set(key, generation_id, ttl=3600)

    @staticmethod
    async def get_active_generation(session_id: str, question_id: str = "default") -> Optional[str]:
        key = ValkeyKeyFactory.active_generation(session_id, question_id)
        return await redis_cache._get(key)

    @staticmethod
    async def cancel_generation(
        session_id: str,
        generation_id: Optional[str] = None,
        question_id: str = "default"
    ) -> int:
        g_key = ValkeyKeyFactory.generation_version(session_id, question_id)
        new_gen_ver = await redis_cache._incr(g_key, ttl=86400)
        if generation_id:
            c_key = ValkeyKeyFactory.generation_cancelled(generation_id)
            await redis_cache._set(c_key, "1", ttl=300)
        return new_gen_ver

    @staticmethod
    async def is_cancelled(generation_id: str) -> bool:
        c_key = ValkeyKeyFactory.generation_cancelled(generation_id)
        flag = await redis_cache._get(c_key)
        return flag == "1"

    @staticmethod
    async def issue_ws_ticket(session_id: str, user_id: str, ttl_seconds: int = 60) -> str:
        import uuid
        ticket = str(uuid.uuid4())
        payload = json.dumps({"session_id": session_id, "user_id": user_id})
        await redis_cache._set(ValkeyKeyFactory.ws_ticket(ticket), payload, ttl=ttl_seconds)
        return ticket

    @staticmethod
    async def consume_ws_ticket(ticket: str, expected_session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        key = ValkeyKeyFactory.ws_ticket(ticket)
        raw = await redis_cache._get(key)
        if not raw:
            return None
        # One-time use: delete immediately on read
        await redis_cache._del(key)
        try:
            data = json.loads(raw)
            if expected_session_id and data.get("session_id") != expected_session_id:
                return None
            return data
        except Exception:
            return None

