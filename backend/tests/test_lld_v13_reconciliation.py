import unittest
import asyncio
import uuid
import time

from app.schemas.websocket_events import (
    WSStandardEnvelope,
    SessionStartEvent,
    AudioFrameEvent,
    TranscriptInterimEvent,
    TranscriptFinalEvent,
    QuestionPredictedEvent,
    AnswerProvisionalStartedEvent,
    AnswerProvisionalDeltaEvent,
    AnswerProvisionalCancelledEvent,
    AnswerFinalStartedEvent,
    AnswerFinalDeltaEvent,
    AnswerFinalCompletedEvent,
    GenerationCancelEvent,
    HeartbeatPingEvent,
    HeartbeatPongEvent
)
from app.cache.redis import ValkeySessionCoordinator, ValkeyKeyFactory
from app.services.predictive_engine import predictive_engine
from app.services.context_orchestrator import context_orchestrator
from app.api.websockets import PriorityOutboundQueue
from app.db.models import (
    TranscriptSegment,
    ConversationTurn,
    QuestionEvent,
    QuestionPrediction,
    AnswerGeneration,
    CandidateProfile,
    CandidateProject,
    SourceProvenance,
    LLMModelRegistryRecord
)

class TestLLDv13Reconciliation(unittest.IsolatedAsyncioTestCase):

    def test_01_lld_v13_envelope_schema(self):
        """Verifies the canonical LLD v1.3 Envelope structure and field types."""
        envelope = WSStandardEnvelope(
            session_id="sess-100",
            event="transcript.final",
            question_id="q-101",
            question_version=2,
            prediction_id="pred-101",
            generation_id="gen-101",
            generation_version=3,
            sequence=18,
            payload={"text": "How does React reconciliation work?", "confidence": 0.98}
        )
        data = envelope.model_dump()
        self.assertEqual(data["protocol_version"], 1)
        self.assertEqual(data["event"], "transcript.final")
        self.assertEqual(data["session_id"], "sess-100")
        self.assertEqual(data["question_id"], "q-101")
        self.assertEqual(data["question_version"], 2)
        self.assertEqual(data["prediction_id"], "pred-101")
        self.assertEqual(data["generation_id"], "gen-101")
        self.assertEqual(data["generation_version"], 3)
        self.assertEqual(data["sequence"], 18)
        self.assertIn("created_at", data)
        self.assertEqual(data["payload"]["confidence"], 0.98)

    def test_02_provisional_and_final_lifecycle_events(self):
        """Verifies typed provisional and final answer streaming schemas."""
        prov_start = AnswerProvisionalStartedEvent(
            session_id="sess-200",
            question_id="q-200",
            generation_id="gen-prov-1",
            model="gemini-3.8-flash"
        )
        self.assertEqual(prov_start.event, "answer.provisional.started")

        prov_delta = AnswerProvisionalDeltaEvent(
            session_id="sess-200",
            question_id="q-200",
            generation_id="gen-prov-1",
            delta_text="React virtual DOM",
            confidence=0.88
        )
        self.assertEqual(prov_delta.event, "answer.provisional.delta")
        self.assertEqual(prov_delta.delta_text, "React virtual DOM")

        prov_cancel = AnswerProvisionalCancelledEvent(
            session_id="sess-200",
            question_id="q-200",
            generation_id="gen-prov-1",
            reason="superseded"
        )
        self.assertEqual(prov_cancel.event, "answer.provisional.cancelled")
        self.assertEqual(prov_cancel.reason, "superseded")

        final_start = AnswerFinalStartedEvent(
            session_id="sess-200",
            question_id="q-200",
            generation_id="gen-final-1",
            model="claude-3-5-sonnet"
        )
        self.assertEqual(final_start.event, "answer.final.started")

        final_delta = AnswerFinalDeltaEvent(
            session_id="sess-200",
            question_id="q-200",
            generation_id="gen-final-1",
            delta_text="The virtual DOM diffing algorithm compares...",
            accumulated_length=45
        )
        self.assertEqual(final_delta.event, "answer.final.delta")

        final_comp = AnswerFinalCompletedEvent(
            session_id="sess-200",
            question_id="q-200",
            generation_id="gen-final-1",
            full_answer="Full answer text.",
            tokens=120,
            duration_ms=450.0
        )
        self.assertEqual(final_comp.event, "answer.final.completed")
        self.assertEqual(final_comp.tokens, 120)

    async def test_03_valkey_hierarchical_keys_and_tickets(self):
        """Verifies ValkeyKeyFactory hierarchical scoping, dual-version increments, and one-time WS tickets."""
        session_id = f"test-sess-{uuid.uuid4()}"
        q1 = "q-1"
        q2 = "q-2"

        self.assertEqual(
            ValkeyKeyFactory.question_version(session_id, q1),
            f"rm:session:{session_id}:question:q-1:version"
        )

        # Question 1 and Question 2 stay isolated
        q1_v, g1_v = await ValkeySessionCoordinator.bump_question_version(session_id, question_id=q1)
        self.assertEqual(q1_v, 2)
        self.assertEqual(g1_v, 2)

        q2_v, g2_v = await ValkeySessionCoordinator.get_state(session_id, question_id=q2)
        self.assertEqual(q2_v, 1)
        self.assertEqual(g2_v, 1)

        # Active generation & cancellation
        gen_id = f"gen-{uuid.uuid4()}"
        await ValkeySessionCoordinator.set_active_generation(session_id, q1, gen_id)
        self.assertEqual(await ValkeySessionCoordinator.get_active_generation(session_id, q1), gen_id)

        bumped_gv = await ValkeySessionCoordinator.cancel_generation(session_id, gen_id, question_id=q1)
        self.assertEqual(bumped_gv, 3)
        self.assertTrue(await ValkeySessionCoordinator.is_cancelled(gen_id))

        # One-time WS ticket
        ticket = await ValkeySessionCoordinator.issue_ws_ticket(session_id, user_id="user-99")
        consumed = await ValkeySessionCoordinator.consume_ws_ticket(ticket, expected_session_id=session_id)
        self.assertIsNotNone(consumed)
        self.assertEqual(consumed["user_id"], "user-99")
        # Second consumption must fail (one-time use)
        self.assertIsNone(await ValkeySessionCoordinator.consume_ws_ticket(ticket, expected_session_id=session_id))

    async def test_04_priority_outbound_queue_coalescing(self):
        """Verifies interim events coalesce under pressure while critical answer chunks are preserved."""
        q = PriorityOutboundQueue(maxsize=4)
        # Fill 3 of 4 slots (75% threshold)
        await q.enqueue({"event": "answer.final.delta", "seq": 1})
        await q.enqueue({"event": "answer.final.delta", "seq": 2})
        await q.enqueue({"event": "answer.final.delta", "seq": 3})

        # Interim event at 75% capacity must coalesce/drop
        accepted_interim = await q.enqueue({"event": "transcript.interim", "text": "hello"})
        self.assertFalse(accepted_interim)
        self.assertEqual(q.dropped_interim_count, 1)

        # Critical final event must still be preserved in the remaining slot
        accepted_final = await q.enqueue({"event": "answer.final.completed", "seq": 4})
        self.assertTrue(accepted_final)
        self.assertEqual(q.qsize, 4)

    def test_05_multifactor_promotion_validator(self):
        """Verifies multi-factor LLD v1.3 Promotion Gate across question, candidate, docs, model, and policy."""
        meta = {
            "question": "What is CAP theorem?",
            "candidate_hash": "hash-abc",
            "doc_versions": ["doc1:v2", "doc2:v1"],
            "model_id": "claude-3-5-sonnet",
            "policy_version": 1
        }
        self.assertTrue(
            predictive_engine.validate_provisional_promotion(
                meta,
                final_question="What is CAP theorem?",
                candidate_profile_hash="hash-abc",
                active_document_versions=["doc2:v1", "doc1:v2"],
                model_id="claude-3-5-sonnet",
                policy_version=1
            )
        )
        # Model mismatch blocks promotion
        self.assertFalse(
            predictive_engine.validate_provisional_promotion(
                meta,
                final_question="What is CAP theorem?",
                candidate_profile_hash="hash-abc",
                active_document_versions=["doc1:v2", "doc2:v1"],
                model_id="gpt-4o",
                policy_version=1
            )
        )

    def test_06_answerability_and_pronoun_resolution(self):
        """Verifies 3-tier answerability guard and follow-up pronoun resolution."""
        # Hypothetical -> applied_reasoning without personal claims
        res_hypo = context_orchestrator.classify_answerability_mode("How would you design a rate limiter?")
        self.assertEqual(res_hypo["mode"], "applied_reasoning")
        self.assertFalse(res_hypo["allow_first_person_claims"])

        # Experience question without evidence -> fallback to technical_knowledge
        res_no_ev = context_orchestrator.classify_answerability_mode("Have you implemented Kafka in production?", "")
        self.assertEqual(res_no_ev["mode"], "technical_knowledge")
        self.assertFalse(res_no_ev["allow_first_person_claims"])

        # Experience question with verified project evidence -> practical_experience
        res_with_ev = context_orchestrator.classify_answerability_mode(
            "Have you implemented Kafka in production?",
            "Built an event-driven Kafka pipeline processing 50k msgs/sec."
        )
        self.assertEqual(res_with_ev["mode"], "practical_experience")
        self.assertTrue(res_with_ev["allow_first_person_claims"])

        # Pronoun resolution
        res_pronoun = context_orchestrator.resolve_pronoun_reference(
            "Have you implemented it?",
            active_entities=["Inheritance", "Polymorphism"]
        )
        self.assertTrue(res_pronoun["resolved"])
        self.assertEqual(res_pronoun["bound_entity"], "Inheritance")

    def test_07_lld_v13_orm_models_instantiation(self):
        """Verifies all new LLD v1.3 SQLAlchemy models instantiate cleanly."""
        seg = TranscriptSegment(session_id="s1", raw_text="raw", corrected_text="corrected")
        turn = ConversationTurn(session_id="s1", question_id="q1", final_question="What is X?")
        q_evt = QuestionEvent(session_id="s1", question_id="q1", question_text="What is X?", status="predicted")
        q_pred = QuestionPrediction(prediction_id="p1", session_id="s1", question_id="q1", predicted_question="What is X?")
        a_gen = AnswerGeneration(generation_id="g1", session_id="s1", question_id="q1", status="started")
        prof = CandidateProfile(user_id="u1", verification_status="imported")
        proj = CandidateProject(profile_id=uuid.uuid4(), user_id="u1", project_name="P1", ownership_role="Lead", summary="Built X")
        prov = SourceProvenance(tenant_id="t1", document_id="d1", access_scope="private")
        reg = LLMModelRegistryRecord(provider="anthropic", model_id="claude-3-5-sonnet")
        self.assertEqual(seg.raw_text, "raw")
        self.assertEqual(turn.question_id, "q1")
        self.assertEqual(q_evt.status, "predicted")
        self.assertEqual(q_pred.prediction_id, "p1")
        self.assertEqual(a_gen.status, "started")
        self.assertEqual(prof.verification_status, "imported")
        self.assertEqual(proj.ownership_role, "Lead")
        self.assertEqual(prov.access_scope, "private")
        self.assertEqual(reg.provider, "anthropic")

if __name__ == "__main__":
    unittest.main()
