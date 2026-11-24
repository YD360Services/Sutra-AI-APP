import asyncio
import json
import logging
import uuid
import time
from typing import Dict, Any, Callable, Awaitable, Optional
from dataclasses import dataclass, field

logger = logging.getLogger("roundmate.job_queue")

@dataclass
class Job:
    id: str
    job_type: str
    payload: Dict[str, Any]
    status: str = "PENDING"  # PENDING, PROCESSING, COMPLETED, FAILED
    retry_count: int = 0
    max_retries: int = 3
    created_at: float = field(default_factory=time.time)
    error: Optional[str] = None

class JobQueueManager:
    """
    Enterprise asynchronous background job queue architecture.
    Provides non-blocking job dispatching, concurrent worker execution,
    automatic retries, and seamless readiness for AWS SQS when scaling.
    """
    def __init__(self, num_workers: int = 2):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Awaitable[Any]]] = {}
        self._workers: list[asyncio.Task] = []
        self._num_workers = num_workers
        self._is_running = False

    def register_handler(self, job_type: str, handler: Callable[[Dict[str, Any]], Awaitable[Any]]):
        """Registers an asynchronous handler function for a specific job type."""
        self._handlers[job_type] = handler
        logger.debug(f"[JobQueue] Registered handler for job_type: '{job_type}'")

    async def enqueue(self, job_type: str, payload: Dict[str, Any], max_retries: int = 3) -> str:
        """Enqueues a job into the processing pipeline without blocking the caller."""
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, job_type=job_type, payload=payload, max_retries=max_retries)
        
        # Check if AWS SQS is configured
        from app.core.config import settings
        sqs_url = getattr(settings, "AWS_SQS_QUEUE_URL", None)
        if sqs_url:
            # SQS Cloud Path
            await self._dispatch_to_sqs(sqs_url, job)
        else:
            # Fast in-memory asynchronous worker pool path
            await self._queue.put(job)

        logger.debug(f"[JobQueue] Enqueued job {job_id} of type '{job_type}'")
        return job_id

    async def _dispatch_to_sqs(self, sqs_url: str, job: Job):
        """Dispatches job message to AWS SQS queue."""
        try:
            import boto3
            sqs = boto3.client('sqs')
            message_body = json.dumps({
                "job_id": job.id,
                "job_type": job.job_type,
                "payload": job.payload,
                "created_at": job.created_at
            })
            await asyncio.to_thread(sqs.send_message, QueueUrl=sqs_url, MessageBody=message_body)
            logger.info(f"[JobQueue] Dispatched job {job.id} to AWS SQS: {sqs_url}")
        except Exception as e:
            logger.warning(f"[JobQueue] Failed to send job to SQS: {e}. Falling back to in-memory queue.")
            await self._queue.put(job)

    async def start_workers(self):
        """Starts background worker pool."""
        if self._is_running:
            return
        self._is_running = True
        for i in range(self._num_workers):
            task = asyncio.create_task(self._worker_loop(f"worker-{i+1}"))
            self._workers.append(task)
        logger.info(f"[JobQueue] Started {self._num_workers} background worker threads.")

    async def stop_workers(self):
        """Gracefully halts background workers."""
        self._is_running = False
        for task in self._workers:
            task.cancel()
        self._workers.clear()

    async def _worker_loop(self, worker_name: str):
        while self._is_running:
            try:
                job: Job = await self._queue.get()
                handler = self._handlers.get(job.job_type)
                
                if not handler:
                    logger.warning(f"[JobQueue:{worker_name}] No handler registered for '{job.job_type}'. Dropping.")
                    self._queue.task_done()
                    continue

                job.status = "PROCESSING"
                try:
                    await handler(job.payload)
                    job.status = "COMPLETED"
                except Exception as exc:
                    job.retry_count += 1
                    job.error = str(exc)
                    if job.retry_count <= job.max_retries:
                        logger.warning(
                            f"[JobQueue:{worker_name}] Job {job.id} failed (attempt {job.retry_count}/{job.max_retries}): {exc}. Retrying..."
                        )
                        await asyncio.sleep(0.5 * job.retry_count)
                        await self._queue.put(job)
                    else:
                        job.status = "FAILED"
                        logger.error(f"[JobQueue:{worker_name}] Job {job.id} permanently failed: {exc}")
                finally:
                    self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[JobQueue:{worker_name}] Unexpected worker error: {e}", exc_info=True)

job_queue = JobQueueManager(num_workers=2)
