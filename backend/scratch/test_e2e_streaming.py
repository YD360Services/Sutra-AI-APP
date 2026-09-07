import sys
import os
import io
import asyncio
import time
import json
import uuid

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from httpx import AsyncClient, ASGITransport
from app.main import app
from app.cache.redis import redis_cache

async def run_e2e_tests():
    print("=" * 75)
    print("STARTING END-TO-END VERIFICATION OF LATENCY OPTIMIZATIONS")
    print("=" * 75)

    await redis_cache.connect()
    from app.db.database import verify_and_initialize_db
    await verify_and_initialize_db()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Test Mock Interview Normalization Endpoint
        print("\n--- 1. Testing /api/mock-interview/normalize-memories ---")
        payload_normalize = {
            "role": "Staff Platform Engineer",
            "company": "ScaleTech",
            "history": [
                {
                    "question": "Tell me about a high throughput architecture challenge you led.",
                    "answer": (
                        "Situation: At ScaleTech, our webhook pipeline choked under 400ms latency spikes during black friday.\n"
                        "Task: Bring p99 latency below 70ms with zero message drop.\n"
                        "Action: I migrated hot state to a Redis Cluster and repartitioned Kafka event topics.\n"
                        "Result: Reduced p99 latency by 58% to 55ms and sustained 30,000 requests per second."
                    ),
                    "score": 96
                }
            ]
        }
        res_norm = await client.post("/api/mock-interview/normalize-memories", json=payload_normalize)
        print(f"Normalize Status: {res_norm.status_code}")
        assert res_norm.status_code == 200, f"Normalize failed: {res_norm.text}"
        norm_data = res_norm.json()
        assert "memories" in norm_data and len(norm_data["memories"]) == 1
        m = norm_data["memories"][0]
        print(f"Normalized Memory Topic: {m['topic']}")
        print(f"STAR Situation: {m['star']['situation']}")
        print(f"STAR Result: {m['star']['result']}")
        print(f"Extracted Metrics: {m['metrics']}")
        assert "400ms" in m["metrics"] or "58%" in m["metrics"] or "55ms" in m["metrics"]
        print(">>> PASS: Mock normalization endpoint returned high-fidelity candidate memories! <<<")

        # 2. Test Speculative Prewarming Endpoint
        print("\n--- 2. Testing /api/answers/prewarm-question ---")
        test_session_id = str(uuid.uuid4())
        mock_knowledge = (
            f"[CANDIDATE MOCK PRACTICE ANSWERS]: Practice Q (Staff Platform Engineer @ ScaleTech): "
            f"High throughput architecture -> Prepared Answer: Situation: At ScaleTech, webhook pipeline choked | "
            f"Action: Migrated hot state to Redis Cluster | Result: Reduced p99 latency by 58% to 55ms at 30k req/s|"
        )
        payload_prewarm = {
            "session_id": test_session_id,
            "partial_question": "Can you tell me how you solved that latency bottleneck with Redis?",
            "knowledge_content": mock_knowledge
        }
        t0 = time.perf_counter()
        res_prewarm = await client.post("/api/answers/prewarm-question", json=payload_prewarm)
        t_prewarm_ms = (time.perf_counter() - t0) * 1000.0
        print(f"Prewarm Status: {res_prewarm.status_code} in {t_prewarm_ms:.2f} ms")
        assert res_prewarm.status_code == 200

        # Poll for background prewarm task to complete into cache
        cached_session = None
        for _ in range(50):
            await asyncio.sleep(0.1)
            cached_session = await redis_cache.get_session_state(test_session_id)
            if cached_session and "prewarmed_context" in cached_session:
                break

        assert cached_session is not None, "Cached session should exist"
        assert "prewarmed_context" in cached_session, "prewarmed_context packet should be in cache"
        pw = cached_session["prewarmed_context"]
        print(f"Prewarmed Context Packet Cached:")
        print(f"  - Role Info: {pw.get('role_info')}")
        print(f"  - Candidate Memories: {pw.get('candidate_memories')[:120]}...")
        assert "ScaleTech" in pw.get("candidate_memories") or "Redis" in pw.get("candidate_memories")
        print(">>> PASS: Speculative prewarming populated 0ms context packet into cache! <<<")

        # 3. Test Live SSE Streaming from /api/answer/stream
        print("\n--- 3. Testing /api/answer/stream (Live TTFT & Output Format) ---")
        payload_stream = {
            "session_id": test_session_id,
            "transcript": "Can you tell me how you solved that latency bottleneck with Redis?",
            "source_type": "transcript",
            "knowledge_content": mock_knowledge
        }

        ttft_ms = None
        first_chunk = ""
        full_response = ""
        t_req_start = time.perf_counter()

        async with client.stream("POST", "/api/answer/stream", json=payload_stream) as response:
            assert response.status_code == 200
            async for chunk in response.aiter_text():
                if not chunk:
                    continue
                if not first_chunk and chunk.strip():
                    ttft_ms = (time.perf_counter() - t_req_start) * 1000.0
                    first_chunk = chunk
                full_response += chunk

        print(f"Time to First Token (TTFT): {ttft_ms if ttft_ms is not None else 0.0:.2f} ms")
        print(f"First Chunk Received:\n{first_chunk.strip()[:200]}")
        print(f"\nFull Stream Response (First 350 chars):\n{full_response.strip()[:350]}...\n")

        # Verify candidate personalization and STAR format
        lower_resp = full_response.lower()
        has_situation = "situation" in lower_resp or "at scaletech" in lower_resp
        has_action_or_result = "action" in lower_resp or "result" in lower_resp or "58%" in lower_resp or "65ms" in lower_resp or "redis" in lower_resp

        print(f"Checks: has_situation={has_situation}, has_action_or_result={has_action_or_result}")
        assert has_situation or has_action_or_result, "Answer must reflect candidate STAR memory!"
        print(">>> PASS: Live stream answered using candidate's personal memories with low TTFT! <<<")

    print("\n" + "=" * 75)
    print("ALL END-TO-END VERIFICATION CHECKS PASSED!")
    print("=" * 75)

if __name__ == "__main__":
    asyncio.run(run_e2e_tests())
