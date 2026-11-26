import unittest
import asyncio

from app.services.transcript_intelligence import transcript_engine
from app.services.context_orchestrator import context_orchestrator
from app.services.interview_intelligence import (
    InterviewIntelligenceGovernance,
    InterviewPatternAggregator,
)
from app.services.rag_service import rag_service, ParallelRetrievalOrchestrator
from app.cache.redis import redis_cache


class TestTable3IntelligenceRAG(unittest.IsolatedAsyncioTestCase):

    def test_01_multi_label_question_classifier(self):
        res = transcript_engine.classify_multi_label_question(
            "Have you used Redis in your project and what do you think about its eviction?"
        )
        label_names = [l["label"] for l in res["labels"]]
        self.assertIn("experience_based", label_names)
        self.assertIn("technical", label_names)
        self.assertIn("opinion_based", label_names)

    async def test_02_conversation_state_versioning(self):
        s1 = await context_orchestrator.update_conversation_state(
            "sess-t3", active_topic="Redis Caching", entities=["Redis", "LRU"]
        )
        self.assertEqual(s1["summary_version"], 1)
        self.assertIn("Redis", s1["entities"])

        s2 = await context_orchestrator.update_conversation_state(
            "sess-t3", active_topic="PostgreSQL", entities=["GIN Index"]
        )
        self.assertEqual(s2["summary_version"], 2)
        self.assertIn("Redis", s2["entities"])
        self.assertIn("GIN Index", s2["entities"])

    def test_03_interview_intelligence_governance_and_aggregation(self):
        # Private session without opt-in must be blocked
        priv = InterviewIntelligenceGovernance.is_eligible_for_shared_intelligence(
            "private_session", opt_in_consent=False
        )
        self.assertFalse(priv["allowed"])

        # Withdrawn consent must be blocked
        withdrawn = InterviewIntelligenceGovernance.is_eligible_for_shared_intelligence(
            "consented_session", opt_in_consent=True, withdrawn=True
        )
        self.assertFalse(withdrawn["allowed"])

        # Aggregation strips PII and only includes consented records
        agg = InterviewPatternAggregator.aggregate_patterns(
            [
                {"source_type": "private_session", "opt_in_consent": False, "topic": "Secret"},
                {"source_type": "consented_session", "opt_in_consent": True, "topic": "Redis user@test.com"},
            ],
            company="Acme",
            role="SDE",
        )
        self.assertEqual(agg["source_count"], 1)
        self.assertIn("Redis [REDACTED_EMAIL]", agg["pattern_frequencies"])
        self.assertIn("RoundMate dataset observation", agg["label"])

    def test_04_reciprocal_rank_fusion_and_checksum(self):
        c1 = rag_service.compute_document_checksum("  hello world  ")
        c2 = rag_service.compute_document_checksum("hello world")
        self.assertEqual(c1, c2)

        vec = [{"id": "d1", "text": "a"}, {"id": "d2", "text": "b"}]
        fts = [{"id": "d2", "text": "b"}, {"id": "d3", "text": "c"}]
        fused = rag_service.reciprocal_rank_fusion(vec, fts, k=60)
        # d2 appears in both lists so it must rank #1
        self.assertEqual(fused[0]["id"], "d2")

    async def test_05_parallel_retrieval_deadline_and_versioned_cache(self):
        async def fast_ok():
            return ["chunk1"]

        async def slow_fail():
            raise RuntimeError("RAG timeout")

        res = await ParallelRetrievalOrchestrator.gather_with_deadline(
            {"fast": fast_ok(), "failing": slow_fail()}, timeout_s=0.5
        )
        self.assertEqual(res["fast"]["status"], "ok")
        self.assertEqual(res["failing"]["status"], "failed")

        # Versioned cache rejects mismatched fingerprint or source_version
        await redis_cache.set_versioned_evidence(
            tenant_id="t1", key="k1", data={"x": 1}, source_version="v2", context_fingerprint="fp-abc"
        )
        self.assertEqual(
            await redis_cache.get_versioned_evidence("t1", "k1", "v2", "fp-abc"),
            {"x": 1},
        )
        self.assertIsNone(
            await redis_cache.get_versioned_evidence("t1", "k1", "v1", "fp-abc")
        )
        self.assertIsNone(
            await redis_cache.get_versioned_evidence("t1", "k1", "v2", "fp-other")
        )


if __name__ == "__main__":
    unittest.main()
