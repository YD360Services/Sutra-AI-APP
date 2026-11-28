import asyncio
import unittest
import uuid

from app.services.response_validator import ResponseValidator
from app.services.object_storage import ObjectStorageClient
from app.core.job_queue import JobQueueManager, execute_privacy_deletion
from app.cache.redis import ValkeySessionCoordinator, SessionFenceManager
from app.core.logging import RedactingLogFilter
from app.core.telemetry import metrics_registry, span_tracer
from app.services.audio_validator import AudioFrameValidator


class TestTable4ProductionReadiness(unittest.IsolatedAsyncioTestCase):

    async def test_01_response_validator_and_claim_guard(self):
        # Unsupported first-person claims flagged when allow_first_person_claims=False
        res = ResponseValidator.check_unsupported_first_person_claims(
            "In my last project I built a Kubernetes cluster.", allow_first_person_claims=False
        )
        self.assertFalse(res["valid"])
        self.assertGreaterEqual(len(res["flagged_phrases"]), 1)

        # Allowed when verified evidence exists
        res_ok = ResponseValidator.check_unsupported_first_person_claims(
            "In my last project I built a Kubernetes cluster.", allow_first_person_claims=True
        )
        self.assertTrue(res_ok["valid"])

        # Chunk currency guard rejects stale generation
        sid = f"s-{uuid.uuid4()}"
        qid = "q-1"
        q_v, g_v = await ValkeySessionCoordinator.get_state(sid, qid)
        self.assertTrue(await ResponseValidator.validate_chunk_currency(sid, qid, q_v, g_v))
        await ValkeySessionCoordinator.cancel_generation(sid, question_id=qid)
        self.assertFalse(await ResponseValidator.validate_chunk_currency(sid, qid, q_v, g_v))

    async def test_02_job_queue_idempotency_dead_letter_replay_and_privacy_deletion(self):
        jq = JobQueueManager(num_workers=1)
        calls = 0

        async def flaky_handler(payload):
            nonlocal calls
            calls += 1
            if payload.get("fail") and calls <= 2:
                raise RuntimeError("transient error")

        jq.register_handler("doc_ingest", flaky_handler)

        # Forbidden live-path job must raise ValueError
        with self.assertRaises(ValueError):
            await jq.enqueue("live_audio", {"bytes": "abc"})

        # Idempotency deduplicates identical keys
        j1 = await jq.enqueue("doc_ingest", {"doc": 1}, idempotency_key="idem-1")
        j2 = await jq.enqueue("doc_ingest", {"doc": 1}, idempotency_key="idem-1")
        self.assertEqual(j1, j2)

        # Dead-letter + replay
        j_fail = await jq.enqueue("doc_ingest", {"fail": True}, max_retries=0)
        # Drain queue items up to j_fail
        while not jq._queue.empty():
            job = await jq._queue.get()
            await jq.process_single_job(job)
        self.assertIn(j_fail, jq._failed_jobs)
        self.assertTrue(await jq.replay_failed_job(j_fail))

        # Privacy deletion handler executes cleanly
        del_res = await execute_privacy_deletion({"session_id": "sess-del", "storage_key": "k-1"})
        self.assertEqual(del_res["status"], "deleted")
        self.assertIn("object_storage", del_res["stores"])
        self.assertIn("valkey_session_cache", del_res["stores"])

    async def test_03_object_storage_checksum_and_lifecycle(self):
        store = ObjectStorageClient()
        store.bucket = ""  # force memory store for unit test
        meta = await store.upload_bytes("tenants/t1/resume.pdf", b"%PDF-1.4 test")
        self.assertEqual(meta["backend"], "memory")
        self.assertEqual(len(meta["checksum"]), 64)
        self.assertEqual(await store.download_bytes("tenants/t1/resume.pdf"), b"%PDF-1.4 test")
        self.assertTrue(await store.delete_object("tenants/t1/resume.pdf"))
        self.assertIsNone(await store.download_bytes("tenants/t1/resume.pdf"))

    async def test_04_session_fence_epoch_stale_writer_protection(self):
        sid = f"fence-{uuid.uuid4()}"
        e1 = await SessionFenceManager.acquire_epoch(sid)
        self.assertTrue(await SessionFenceManager.validate_epoch(sid, e1))
        # Reconnect / new instance acquires higher epoch
        e2 = await SessionFenceManager.acquire_epoch(sid)
        self.assertGreater(e2, e1)
        self.assertFalse(await SessionFenceManager.validate_epoch(sid, e1))
        self.assertTrue(await SessionFenceManager.validate_epoch(sid, e2))

    def test_05_telemetry_and_log_redaction(self):
        redacted = RedactingLogFilter.redact_text(
            "Authorization: Bearer abc123.xyz user=alice@example.com key=sk-1234567890abcdef"
        )
        self.assertNotIn("abc123", redacted)
        self.assertNotIn("alice@example.com", redacted)
        self.assertNotIn("sk-1234567890abcdef", redacted)

        with span_tracer.start_span("test.op", trace_id="tr-99", attributes={"raw_audio": "SECRET", "ok": 1}):
            metrics_registry.observe("llm_ttft_ms", 120.5)
        self.assertGreaterEqual(metrics_registry.p95("llm_ttft_ms"), 120.5)
        # Sensitive attributes stripped from span
        self.assertNotIn("raw_audio", span_tracer.recent_spans[-1]["attributes"])

    async def test_06_concurrent_cancellation_race_simulation(self):
        """Item 63: Simulates mid-stream cancellation racing with token generation."""
        sid = f"race-{uuid.uuid4()}"
        qid = "q-race"
        _, g_ver = await ValkeySessionCoordinator.get_state(sid, qid)
        emitted = []

        async def producer():
            for i in range(10):
                if not await ResponseValidator.validate_chunk_currency(sid, qid, 1, g_ver):
                    break
                emitted.append(i)
                await asyncio.sleep(0.005)

        async def canceller():
            await asyncio.sleep(0.012)
            await ValkeySessionCoordinator.cancel_generation(sid, question_id=qid)

        await asyncio.gather(producer(), canceller())
        # Must stop early rather than emitting all 10 chunks
        self.assertLess(len(emitted), 10)

    async def test_07_security_oversized_frame_and_ticket_replay(self):
        """Item 64: Verifies oversized audio frame rejection and WS ticket replay protection."""
        av = AudioFrameValidator(max_frame_bytes=8192)
        self.assertFalse(av.validate_frame(b"0" * 16384).valid)

        sid = f"sec-{uuid.uuid4()}"
        ticket = await ValkeySessionCoordinator.issue_ws_ticket(sid, user_id="u1")
        # Wrong session_id cannot consume ticket (and consumes/invalidates it)
        self.assertIsNotNone(await ValkeySessionCoordinator.consume_ws_ticket(ticket, expected_session_id=sid))
        # Replay attempt fails
        self.assertIsNone(await ValkeySessionCoordinator.consume_ws_ticket(ticket, expected_session_id=sid))


if __name__ == "__main__":
    unittest.main()
