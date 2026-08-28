import redis.asyncio as redis
import json
import logging
from typing import Optional, Dict, Any
from app.core.config import settings

logger = logging.getLogger("copilotx.redis")

class RedisCache:
    def __init__(self):
        self.redis_url = settings.REDIS_URL
        self._client: Optional[redis.Redis] = None
        self._local_cache: Dict[str, Any] = {}

    async def connect(self):
        try:
            self._client = redis.from_url(self.redis_url, decode_responses=True)
            # Ping to verify connection
            await self._client.ping()
            logger.info("Connected to Redis successfully.")
        except Exception as e:
            logger.warning(f"Redis connection failed (running in fallback/in-memory mode): {e}")
            self._client = None

    async def disconnect(self):
        if self._client:
            await self._client.close()

    async def get_session_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        key = f"session:{session_id}"
        if not self._client:
            return self._local_cache.get(key)
        try:
            data = await self._client.get(key)
            if data:
                return json.loads(data)
            return self._local_cache.get(key)
        except Exception as e:
            logger.warning(f"Redis error getting session state: {e}")
            return self._local_cache.get(key)

    async def set_session_state(self, session_id: str, state: Dict[str, Any], expire_seconds: int = 14400):
        key = f"session:{session_id}"
        self._local_cache[key] = state
        if not self._client:
            return
        try:
            await self._client.setex(
                key,
                expire_seconds,
                json.dumps(state)
            )
        except Exception as e:
            logger.warning(f"Redis error setting session state: {e}")

    async def get_transcript(self, session_id: str) -> Optional[str]:
        key = f"transcript:{session_id}"
        if not self._client:
            return self._local_cache.get(key)
        try:
            res = await self._client.get(key)
            if res:
                return res
            return self._local_cache.get(key)
        except Exception as e:
            logger.warning(f"Redis error getting transcript: {e}")
            return self._local_cache.get(key)

    async def set_transcript(self, session_id: str, transcript: str, expire_seconds: int = 14400):
        key = f"transcript:{session_id}"
        self._local_cache[key] = transcript
        if not self._client:
            return
        try:
            await self._client.setex(
                key,
                expire_seconds,
                transcript
            )
        except Exception as e:
            logger.warning(f"Redis error setting transcript: {e}")

    async def get_prepared_prompt(self, session_id: str) -> Optional[str]:
        return await self.get_cached_item(f"prepared_prompt:{session_id}")

    async def set_prepared_prompt(self, session_id: str, prompt: str, expire_seconds: int = 14400):
        await self.set_cached_item(f"prepared_prompt:{session_id}", prompt, expire_seconds)

    async def track_websocket(self, session_id: str, client_id: str, register: bool = True):
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
            logger.warning(f"Redis error tracking websocket: {e}")

    async def count_active_websockets(self, session_id: str) -> int:
        if not self._client:
            return 0
        try:
            return await self._client.scard(f"ws:{session_id}")
        except Exception as e:
            logger.warning(f"Redis error counting websockets: {e}")
            return 0

    async def get_cached_item(self, key: str) -> Optional[str]:
        if self._client:
            try:
                res = await self._client.get(key)
                if res:
                    return res
            except Exception as e:
                logger.warning(f"Redis error getting cache key {key}: {e}")
        return self._local_cache.get(key)

    async def set_cached_item(self, key: str, content: str, expire_seconds: int = 14400):
        self._local_cache[key] = content
        if self._client:
            try:
                await self._client.setex(key, expire_seconds, content)
            except Exception as e:
                logger.warning(f"Redis error setting cache key {key}: {e}")

    async def delete_cached_item(self, key: str):
        self._local_cache.pop(key, None)
        if self._client:
            try:
                await self._client.delete(key)
            except Exception as e:
                logger.warning(f"Redis error deleting cache key {key}: {e}")

    async def get_resume(self, resume_id: str) -> Optional[str]:
        return await self.get_cached_item(f"resume:{resume_id}")

    async def set_resume(self, resume_id: str, content: str, expire_seconds: int = 14400):
        await self.set_cached_item(f"resume:{resume_id}", content, expire_seconds)

redis_cache = RedisCache()
