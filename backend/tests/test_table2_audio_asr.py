import unittest

from app.services.audio_validator import AudioFrameValidator
from app.services.deepgram_service import DeepgramService, ASRReconnectManager
from app.services.transcript_vocab import correct_transcript_with_confidence
from app.services.transcript_corpus import evaluate_golden_corpus


class TestTable2AudioASRPipeline(unittest.TestCase):

    def test_01_audio_frame_validator_bounds_and_sequences(self):
        v = AudioFrameValidator(max_frame_bytes=16000)
        self.assertTrue(v.validate_format(16000, 1, "pcm_s16le"))
        self.assertFalse(v.validate_format(44100, 2, "pcm_s16le"))

        # Empty or oversized frames fail
        self.assertFalse(v.validate_frame(b"").valid)
        self.assertFalse(v.validate_frame(b"x" * 20000).valid)

        # Sequence checks: 1 -> 2 -> 2 (dup) -> 1 (ooo) -> 5 (gap)
        self.assertTrue(v.validate_frame(b"a" * 8000, sequence=1).valid)
        self.assertTrue(v.validate_frame(b"a" * 8000, sequence=2).valid)

        dup = v.validate_frame(b"a" * 8000, sequence=2)
        self.assertFalse(dup.valid)
        self.assertTrue(dup.is_duplicate)

        ooo = v.validate_frame(b"a" * 8000, sequence=1)
        self.assertFalse(ooo.valid)
        self.assertTrue(ooo.is_out_of_order)

        gap = v.validate_frame(b"a" * 8000, sequence=5)
        self.assertTrue(gap.valid)
        self.assertTrue(gap.gap_detected)
        self.assertEqual(gap.expected_sequence, 3)

    def test_02_deepgram_normalization_and_asr_reconnect_gap(self):
        raw = {
            "is_final": True,
            "speech_final": True,
            "start": 1.25,
            "duration": 0.75,
            "metadata": {"request_id": "dg-req-42"},
            "channel": {"alternatives": [{"transcript": "Explain Redis", "confidence": 0.94}]},
        }
        norm = DeepgramService.normalize_event(raw)
        self.assertIsNotNone(norm)
        self.assertEqual(norm.provider, "deepgram")
        self.assertEqual(norm.raw_text, "Explain Redis")
        self.assertEqual(norm.confidence, 0.94)
        self.assertEqual(norm.request_id, "dg-req-42")
        self.assertEqual(norm.start_ms, 1250.0)
        self.assertEqual(norm.duration_ms, 750.0)

        mgr = ASRReconnectManager(max_retries=2, base_delay_s=0.5)
        self.assertTrue(mgr.can_retry())
        d1 = mgr.next_backoff_seconds()
        self.assertGreaterEqual(d1, 0.5)
        self.assertTrue(mgr.can_retry())
        mgr.next_backoff_seconds()
        self.assertFalse(mgr.can_retry())

        gap_evt = mgr.create_gap_marker("deepgram", reason="upstream_closed")
        self.assertTrue(gap_evt.is_gap)
        self.assertEqual(gap_evt.raw_text, "")
        self.assertEqual(gap_evt.gap_reason, "upstream_closed")

    def test_03_transcript_correction_golden_corpus(self):
        report = evaluate_golden_corpus()
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["passed"], report["total"])
        self.assertEqual(report["accuracy"], 1.0)


if __name__ == "__main__":
    unittest.main()
