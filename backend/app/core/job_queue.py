"""
LLD v1.3 Items 47, 48 & 49: Valkey/ARQ-Compatible Background Job Queue & Privacy Deletion.
- Never enqueues live audio, interim transcripts, or answer deltas.
- Enforces idempotency_key deduplication, bounded retries, dead-lettering, and replay.
- Isolates queue workload from live session keys.
"""
import asyncio
import json
import logging
import uuid
import time
from typing import Dict, Any, Callable, Awaitable, Optional, List
from dataclasses import dataclass, field

logger = logging.getLogger("roundmate.job_queue")

FORBIDDEN_LIVE_JOB_TYPES = {"live_audio", "interim_transcript", "answer_delta", "audio_frame"}


@dataclass
class Job:
    id: str
    job_type: str
    payload: Dict[str, Any]
    idempotency_key: Optional[str] = None
    correlation_id: Optional[str] = None
    tenant_id: Optional[str] = None
    status: str = "PENDING"  # PENDING, PROCESSING, COMPLETED, FAILED, DEAD_LETTER
    retry_count: int = 0
    max_retries: int = 3
    created_at: float = field(default_factory=time.time)
    error: Optional[str] = None


class JobQueueManager:
    """
    Render Background Worker & ARQ-compatible job manager backed by Valkey or in-memory pool.
    """
    def __init__(self, num_workers: int = 2):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Awaitable[Any]]] = {}
        self._workers: List[asyncio.Task] = []
        self._num_workers = num_workers
        self._is_running = False
        self._failed_jobs: Dict[str, Job] = {}
        self._seen_idempotency: Dict[str, str] = {}

    def register_handler(self, job_type: str, handler: Callable[[Dict[str, Any]], Awaitable[Any]]):
        self._handlers[job_type] = handler
        logger.debug(f"[JobQueue] Registered handler for job_type: '{job_type}'")

    async def enqueue(
        self,
        job_type: str,
        payload: Dict[str, Any],
        max_retries: int = 3,
        idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> str:
        if job_type in FORBIDDEN_LIVE_JOB_TYPES:
            raise ValueError(f"Forbidden live-path job type '{job_type}' cannot be enqueued to background workers.")

        if idempotency_key and idempotency_key in self._seen_idempotency:
            return self._seen_idempotency[idempotency_key]

        job_id = str(uuid.uuid4())
        if idempotency_key:
            self._seen_idempotency[idempotency_key] = job_id

        job = Job(
            id=job_id,
            job_type=job_type,
            payload=payload,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id or str(uuid.uuid4()),
            tenant_id=tenant_id,
            max_retries=max_retries,
        )

        # Persist to Valkey broker list if Redis/Valkey is active
        from app.cache.redis import redis_cache
        if redis_cache._redis_available and redis_cache._client:
            try:
                await redis_cache._client.rpush("rm:arq:jobs", json.dumps({
                    "id": job.id,
                    "job_type": job.job_type,
                    "payload": job.payload,
                    "idempotency_key": job.idempotency_key,
                    "correlation_id": job.correlation_id,
                    "tenant_id": job.tenant_id,
                }))
            except Exception as e:
                logger.debug(f"[JobQueue] Valkey push fallback to local queue: {e}")

        await self._queue.put(job)
        return job_id

    async def process_single_job(self, job: Job) -> bool:
        handler = self._handlers.get(job.job_type)
        if not handler:
            job.status = "DEAD_LETTER"
            job.error = f"No handler registered for '{job.job_type}'"
            self._failed_jobs[job.id] = job
            return False

        job.status = "PROCESSING"
        try:
            await handler(job.payload)
            job.status = "COMPLETED"
            return True
        except Exception as exc:
            job.retry_count += 1
            job.error = str(exc)
            if job.retry_count <= job.max_retries:
                job.status = "PENDING"
                await self._queue.put(job)
            else:
                job.status = "DEAD_LETTER"
                self._failed_jobs[job.id] = job
            return False

    async def replay_failed_job(self, job_id: str) -> bool:
        job = self._failed_jobs.pop(job_id, None)
        if not job:
            return False
        job.retry_count = 0
        job.status = "PENDING"
        job.error = None
        await self._queue.put(job)
        return True

    async def start_workers(self):
        if self._is_running:
            return
        self._is_running = True
        for i in range(self._num_workers):
            self._workers.append(asyncio.create_task(self._worker_loop(f"worker-{i+1}")))

    async def stop_workers(self):
        self._is_running = False
        for task in self._workers:
            task.cancel()
        self._workers.clear()

    async def _worker_loop(self, worker_name: str):
        while self._is_running:
            try:
                job: Job = await self._queue.get()
                await self.process_single_job(job)
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[JobQueue:{worker_name}] Loop error: {exc}")


async def execute_privacy_deletion(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    LLD v1.3 Item 49: Idempotent cascading privacy deletion across derived stores
    (object storage, vector chunks, caches, and DB records).
    """
    from app.services.object_storage import object_storage
    from app.cache.redis import redis_cache

    tenant_id = payload.get("tenant_id")
    user_id = payload.get("user_id")
    storage_key = payload.get("storage_key")
    session_id = payload.get("session_id")

    deleted_stores = []
    if storage_key:
        await object_storage.delete_object(storage_key)
        deleted_stores.append("object_storage")

    if session_id:
        await redis_cache.delete_cached_item(f"session:{session_id}")
        await redis_cache.delete_cached_item(f"transcript:{session_id}")
        await redis_cache.delete_cached_item(f"rm:session:{session_id}:conv_state")
        deleted_stores.append("valkey_session_cache")

    if user_id or tenant_id:
        try:
            from app.db import database
            from app.db.models import SemanticEmbedding, SourceProvenance
            from sqlalchemy import delete
            if database.SessionLocal is not None:
                async with database.SessionLocal() as db:
                    if user_id:
                        await db.execute(delete(SemanticEmbedding).where(SemanticEmbedding.user_id == str(user_id)))
                    if tenant_id:
                        await db.execute(delete(SourceProvenance).where(SourceProvenance.tenant_id == str(tenant_id)))
                    await db.commit()
                    deleted_stores.append("postgres_vectors_provenance")
        except Exception as e:
            logger.debug(f"[PrivacyDeletion] DB cleanup skipped/partial: {e}")

    return {"status": "deleted", "stores": deleted_stores}


job_queue = JobQueueManager()
job_queue.register_handler("privacy_deletion", execute_privacy_deletion)
