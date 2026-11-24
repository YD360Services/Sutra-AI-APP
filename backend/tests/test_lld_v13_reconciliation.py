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
from app.cache.redis import ValkeySessionCoordinator
from app.services.predictive_engine import predictive_engine

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

    async def test_03_valkey_session_coordinator_state(self):
        """Verifies ValkeySessionCoordinator dual-version increments and cancellation."""
        session_id = f"test-sess-{uuid.uuid4()}"

        q_v, g_v = await ValkeySessionCoordinator.get_state(session_id)
        self.assertEqual(q_v, 1)
        self.assertEqual(g_v, 1)

        # Inferred question intent shifts -> bumps both question_version and generation_version
        new_qv, new_gv = await ValkeySessionCoordinator.bump_question_version(session_id)
        self.assertEqual(new_qv, 2)
        self.assertEqual(new_gv, 2)

        # Cancel active generation
        gen_id = f"gen-{uuid.uuid4()}"
        bumped_gv = await ValkeySessionCoordinator.cancel_generation(session_id, gen_id)
        self.assertEqual(bumped_gv, 3)
        self.assertTrue(await ValkeySessionCoordinator.is_cancelled(gen_id))

    def test_04_predictive_promotion_fingerprint_gate(self):
        """Verifies promotion gate logic: exact fingerprint match allows promotion, divergence blocks it."""
        question_text = "What is the difference between TCP and UDP?"
        context_str = "Candidate has networking fundamentals in resume."
        policy_ver = 1

        prov_fingerprint = predictive_engine.compute_fingerprint(question_text, context_str, policy_ver)

        # Exact match -> eligible for promotion
        self.assertTrue(
            predictive_engine.can_promote_provisional(
                prov_fingerprint,
                final_question=question_text,
                final_context=context_str,
                policy_version=policy_ver
            )
        )

        # Divergent question (speaker finished with a qualifier) -> rejects promotion
        divergent_q = "What is the difference between TCP and UDP in HTTP/3?"
        self.assertFalse(
            predictive_engine.can_promote_provisional(
                prov_fingerprint,
                final_question=divergent_q,
                final_context=context_str,
                policy_version=policy_ver
            )
        )

    def test_05_client_events_generation_cancel_and_heartbeat(self):
        """Verifies client control events: generation.cancel, heartbeat.ping/pong, audio.frame."""
        cancel_evt = GenerationCancelEvent(session_id="sess-300", reason="interrupted")
        self.assertEqual(cancel_evt.event, "generation.cancel")
        self.assertEqual(cancel_evt.reason, "interrupted")

        ping_evt = HeartbeatPingEvent(session_id="sess-300")
        self.assertEqual(ping_evt.event, "heartbeat.ping")

        pong_evt = HeartbeatPongEvent(session_id="sess-300")
        self.assertEqual(pong_evt.event, "heartbeat.pong")

        audio_evt = AudioFrameEvent(session_id="sess-300", frame_ms=250, sample_rate=16000)
        self.assertEqual(audio_evt.event, "audio.frame")
        self.assertEqual(audio_evt.sample_rate, 16000)
        self.assertEqual(audio_evt.frame_ms, 250)

if __name__ == "__main__":
    unittest.main()
