import time
import json
import logging
from typing import Dict, Any, Optional, List
from app.services.transcript_intelligence import transcript_engine, PATTERN_REGISTRY
from app.cache.redis import redis_cache

logger = logging.getLogger("roundmate.predictive_engine")

class PredictiveQuestionEngine:
    """
    Predictive Question Engine.
    Listens to interim streaming transcript events while the interviewer is still speaking,
    extrapolates the full question, and pre-warms context and answer pipelines
    1-2 seconds before speech finishes.
    """

    def __init__(self):
        self._min_words_for_prediction = 3

    def predict_from_interim(
        self,
        interim_text: str,
        recent_technologies: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Fast heuristic prediction: evaluates incoming partial speech in < 5ms.
        Returns predicted question, confidence score, intent, and category.
        """
        if not interim_text:
            return {"predicted": False, "question": "", "confidence": 0.0}

        clean = transcript_engine.normalize(interim_text)
        words = clean.split()
        if len(words) < self._min_words_for_prediction:
            return {"predicted": False, "question": "", "confidence": 0.0}

        # Extract features
        detected_tech = transcript_engine.detect_technologies(clean)
        if recent_technologies:
            for t in recent_technologies:
                if t not in detected_tech:
                    detected_tech.append(t)

        detected_kws = transcript_engine.extract_keywords(clean)
        intent = transcript_engine.detect_intent(clean)
        category = transcript_engine.detect_category(clean)
        question_started = transcript_engine.detect_question_started(clean)

        if not question_started and "?" not in interim_text:
            # Low confidence that an interview question has begun
            return {
                "predicted": False,
                "question": "",
                "confidence": 0.2,
                "intent": intent,
                "category": category
            }

        # 1. Check exact pattern matches in PATTERN_REGISTRY
        predicted_question = transcript_engine.predict_question(clean, detected_tech, detected_kws)
        confidence = 0.85 if detected_tech or detected_kws else 0.65

        # 2. Grammar formatting if heuristic fallback was used
        if predicted_question.startswith("Can you explain?"):
            if len(clean) > 10:
                capitalized = clean[0].upper() + clean[1:]
                if not capitalized.endswith("?"):
                    capitalized += "?"
                predicted_question = capitalized
                confidence = 0.50

        return {
            "predicted": True,
            "question": predicted_question,
            "confidence": confidence,
            "detected_technologies": detected_tech,
            "detected_keywords": detected_kws,
            "intent": intent,
            "category": category,
            "interim_length": len(words)
        }

    async def prewarm_session_prediction(
        self,
        session_id: str,
        interim_text: str,
        user_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Asynchronously pre-warms candidate context in Redis while speech is incoming.
        Saves up to 1,500ms of latency when the interviewer finishes asking the question.
        """
        prediction = self.predict_from_interim(interim_text)
        if not prediction.get("predicted") or prediction.get("confidence", 0) < 0.60:
            return None

        predicted_q = prediction["question"]
        t_start = time.time()

        # Cache predicted question state in Redis with short TTL (30 seconds)
        try:
            cache_payload = {
                "predicted_question": predicted_q,
                "confidence": prediction["confidence"],
                "category": prediction["category"],
                "intent": prediction["intent"],
                "technologies": prediction["detected_technologies"],
                "timestamp": time.time()
            }
            # Store in session state cache
            cached_session = await redis_cache.get_session_state(session_id) or {}
            cached_session["latest_prediction"] = cache_payload
            await redis_cache.set_session_state(session_id, cached_session)

            logger.info(
                f"[PredictiveEngine] Pre-warmed prediction for session {session_id} "
                f"in {(time.time() - t_start)*1000:.2f}ms: '{predicted_q}' (conf={prediction['confidence']})"
            )
            return cache_payload
        except Exception as e:
            logger.debug(f"[PredictiveEngine] Pre-warm caching skipped: {e}")
            return None

    def compute_fingerprint(self, question: str, context: Optional[str] = None, policy_version: int = 1) -> str:
        """
        Computes SHA256 context/input fingerprint for the LLD v1.3 Promotion Gate.
        A provisional answer can only be promoted to final answer if fingerprints match exactly.
        """
        import hashlib
        raw = f"{question.strip().lower()}|{(context or '').strip()}|{policy_version}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def can_promote_provisional(
        self,
        provisional_fingerprint: str,
        final_question: str,
        final_context: Optional[str] = None,
        policy_version: int = 1
    ) -> bool:
        """
        Evaluates exact equality invariant between provisional and final intent.
        Returns True only if the candidate's provisional context matches the final question.
        """
        final_fingerprint = self.compute_fingerprint(final_question, final_context, policy_version)
        return provisional_fingerprint == final_fingerprint

predictive_engine = PredictiveQuestionEngine()
