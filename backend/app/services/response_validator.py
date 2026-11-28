"""
LLD v1.3 Items 44 & 45: Streaming Answer Validation & Provenance Persistence.
- Per-chunk validation against session, question_version, and generation_version.
- Deterministic check for unsupported first-person claims when candidate lacks verified evidence.
- Idempotent persistence of AnswerGeneration records.
"""
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from app.cache.redis import ValkeySessionCoordinator


_FIRST_PERSON_CLAIM_RE = re.compile(
    r"\b(i built|i implemented|i designed|i deployed|in my last project|at my previous company|my team and i)\b",
    re.IGNORECASE,
)


class ResponseValidator:
    @staticmethod
    async def validate_chunk_currency(
        session_id: str,
        question_id: str,
        expected_q_ver: int,
        expected_gen_ver: int,
    ) -> bool:
        """Rejects late/stale chunks even when upstream provider cancellation is best-effort."""
        curr_q_v, curr_g_v = await ValkeySessionCoordinator.get_state(session_id, question_id=question_id)
        return curr_q_v == expected_q_ver and curr_g_v == expected_gen_ver

    @staticmethod
    def check_unsupported_first_person_claims(
        answer_text: str,
        allow_first_person_claims: bool,
    ) -> Dict[str, Any]:
        """Deterministic guard against fabricated personal experience claims."""
        if allow_first_person_claims or not answer_text:
            return {"valid": True, "flagged_phrases": []}
        matches = _FIRST_PERSON_CLAIM_RE.findall(answer_text)
        return {
            "valid": len(matches) == 0,
            "flagged_phrases": matches,
        }


class AnswerProvenanceService:
    @staticmethod
    async def persist_generation(
        db,
        generation_id: str,
        session_id: str,
        question_id: str,
        question_version: int,
        generation_version: int,
        is_provisional: bool,
        status: str,
        provider: Optional[str] = None,
        model_id: Optional[str] = None,
        context_fingerprint: Optional[str] = None,
        answer_text: Optional[str] = None,
        superseded_by_id: Optional[str] = None,
    ) -> None:
        """Idempotent write to answer_generations table."""
        from sqlalchemy import select
        from app.db.models import AnswerGeneration

        res = await db.execute(select(AnswerGeneration).where(AnswerGeneration.generation_id == generation_id))
        existing = res.scalar_one_or_none()
        if existing:
            existing.status = status
            if answer_text is not None:
                existing.answer_text = answer_text
            if superseded_by_id is not None:
                existing.superseded_by_id = superseded_by_id
            if status == "completed":
                existing.completed_at = datetime.utcnow()
        else:
            db.add(
                AnswerGeneration(
                    generation_id=generation_id,
                    session_id=session_id,
                    question_id=question_id,
                    question_version=question_version,
                    generation_version=generation_version,
                    is_provisional=is_provisional,
                    status=status,
                    provider=provider,
                    model_id=model_id,
                    context_fingerprint=context_fingerprint,
                    superseded_by_id=superseded_by_id,
                    answer_text=answer_text,
                    completed_at=datetime.utcnow() if status == "completed" else None,
                )
            )
        await db.commit()
