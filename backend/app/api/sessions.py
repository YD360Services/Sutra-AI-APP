from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional, Dict, Set
from datetime import datetime
import uuid

from app.db.database import get_db
from app.db.models import User
from app.core.deps import get_current_user_optional, verify_session_ownership
from app.db.repositories import SessionRepository
from app.schemas.session import SessionCreate, SessionUpdate, SessionResponse
from app.core.user_utils import normalize_user_id

router = APIRouter()

# LLD v1.3 Explicit Session Lifecycle State Machine
VALID_SESSION_TRANSITIONS: Dict[str, Set[str]] = {
    "created": {"active", "paused", "completed", "ended"},
    "active": {"paused", "completed", "ended"},
    "paused": {"active", "completed", "ended"},
    "completed": set(),
    "ended": set(),
}


def validate_session_transition(current_status: str, target_status: str) -> bool:
    """Validates state transition. Same-state transitions are idempotent (True)."""
    curr = (current_status or "active").lower()
    tgt = (target_status or "").lower()
    if curr == tgt:
        return True
    allowed = VALID_SESSION_TRANSITIONS.get(curr, set())
    return tgt in allowed

@router.post("/sessions", response_model=SessionResponse)
async def start_session(
    payload: SessionCreate,
    user_id: Optional[str] = None,  # Mock user auth dependency in demo
    db: AsyncSession = Depends(get_db)
):
    normalized_user_id = normalize_user_id(user_id)
    repo = SessionRepository(db)
    
    session_name = payload.session_name or f"Mock Prep Session with {payload.company_name}"
    
    session = await repo.create(
        session_name=session_name,
        company_name=payload.company_name,
        role_name=payload.role_name,
        language=payload.language,
        audio_source=payload.audio_source,
        user_id=normalized_user_id,
        job_description_id=payload.job_description_id
    )
    await db.commit()
    await db.refresh(session)
    return session

@router.get("/sessions", response_model=List[SessionResponse])
async def list_sessions(
    user_id: Optional[str] = None,
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db)
):
    repo = SessionRepository(db)
    target_uuid = current_user.id if current_user else normalize_user_id(user_id)
    if not target_uuid:
        return []
    sessions = await repo.list_by_user(user_id=target_uuid)
    return sessions

async def generate_session_summary_if_needed(session_id: uuid.UUID, db: AsyncSession, repo: SessionRepository) -> Optional[str]:
    import logging
    logger = logging.getLogger("roundmate.sessions")
    try:
        from app.cache.redis import redis_cache
        from app.db.repositories import TranscriptRepository, QARepository
        from app.services.ai_service import call_llm

        db_session = await repo.get_by_id(session_id)
        if not db_session:
            return None

        company = db_session.company_name or "Target Company"
        role = db_session.role_name or "Software Engineer"

        # 1. Retrieve Redis transcript if available
        transcript_text = ""
        try:
            redis_transcript = await redis_cache.get_transcript(str(session_id))
            if redis_transcript and redis_transcript.strip():
                transcript_text = redis_transcript.strip()
        except Exception:
            pass

        # 2. Retrieve PostgreSQL database transcripts
        t_repo = TranscriptRepository(db)
        qa_repo = QARepository(db)

        db_transcripts = await t_repo.list_by_session(session_id)
        db_qas = await qa_repo.list_by_session(session_id)

        combined_log_parts = []
        if transcript_text:
            combined_log_parts.append(f"Full Audio Stream Transcript:\n{transcript_text}")
        elif db_transcripts:
            for t in db_transcripts:
                speaker_label = "Interviewer" if t.speaker == "interviewer" else ("Candidate" if t.speaker == "you" else ("Session Audio" if t.speaker == "full_session" else t.speaker))
                combined_log_parts.append(f"[{speaker_label}]: {t.content}")

        if db_qas:
            combined_log_parts.append("\nQuestions & Answers Exchanged:")
            for qa in db_qas:
                combined_log_parts.append(f"Question: {qa.question}\nAnswer Given: {qa.answer}")

        full_session_log = "\n\n".join(combined_log_parts).strip()

        if not full_session_log:
            logger.info(f"No transcript or QA logs available to generate summary for session {session_id}")
            return None

        summary_prompt = f"""
Analyze the following interview preparation session for a {role} position at {company}.
Create a high-impact, professional summary and performance breakdown of the session.

Session Transcript & Questions Log:
{full_session_log}

Please format the summary with the following clear markdown structure:
### 1. Executive Summary
- Brief 2-sentence overview of the session scope and performance.

### 2. Key Questions & Topics Covered
- Bulleted list of primary technical and behavioral interview questions asked.

### 3. Key Strengths Demonstrated
- Highlights of strong responses, algorithmic concepts, and system architecture depth.

### 4. Actionable Improvements & Next Steps
- Constructive feedback on edge cases to cover, clearer explanations, and areas for improvement in future rounds.
"""
        system_prompt = "You are a Principal Software Engineer and hiring manager. Generate an actionable, structured, professional markdown summary of this interview session."
        summary_text = await call_llm(
            prompt=summary_prompt,
            system_prompt=system_prompt,
            temperature=0.3
        )
        if summary_text and summary_text.strip():
            updated = await repo.update(session_id, summary=summary_text.strip())
            await db.commit()
            return summary_text.strip()
    except Exception as err:
        logger.error(f"Error generating session summary: {err}", exc_info=True)
    return None

@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    repo = SessionRepository(db)
    session = await repo.get_by_id(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    # If summary is missing, attempt to generate it dynamically
    if not session.summary:
        generated_summary = await generate_session_summary_if_needed(session_id, db, repo)
        if generated_summary:
            session.summary = generated_summary
    return session

@router.post("/sessions/{session_id}/generate-summary", response_model=SessionResponse)
async def trigger_generate_summary(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    repo = SessionRepository(db)
    session = await repo.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    generated_summary = await generate_session_summary_if_needed(session_id, db, repo)
    if generated_summary:
        session.summary = generated_summary
    return session

@router.patch("/sessions/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: uuid.UUID,
    payload: SessionUpdate,
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db)
):
    repo = SessionRepository(db)
    existing = await verify_session_ownership(session_id, current_user, db, allow_anonymous_legacy=True)
    update_data = payload.dict(exclude_unset=True)

    if "status" in update_data and update_data["status"]:
        if not validate_session_transition(existing.status, update_data["status"]):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Invalid session status transition from '{existing.status}' to '{update_data['status']}'."
            )
        if update_data["status"].lower() in ("completed", "ended") and not existing.ended_at:
            update_data["ended_at"] = datetime.utcnow()

    session = await repo.update(session_id, **update_data)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    await db.commit()
    await db.refresh(session)

    # Generate summary & save transcripts if completing the session
    if payload.status in ("completed", "ended") and not session.summary:
        generated_summary = await generate_session_summary_if_needed(session_id, db, repo)
        if generated_summary:
            session.summary = generated_summary

    return session


async def _transition_session_lifecycle(
    session_id: uuid.UUID,
    target_status: str,
    current_user: Optional[User],
    db: AsyncSession,
):
    repo = SessionRepository(db)
    existing = await verify_session_ownership(session_id, current_user, db, allow_anonymous_legacy=True)
    if existing.status == target_status:
        return existing  # Idempotent duplicate request
    if not validate_session_transition(existing.status, target_status):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot transition session from '{existing.status}' to '{target_status}'."
        )
    kwargs = {"status": target_status}
    if target_status in ("completed", "ended") and not existing.ended_at:
        kwargs["ended_at"] = datetime.utcnow()
    updated = await repo.update(session_id, **kwargs)
    await db.commit()
    await db.refresh(updated)
    return updated


@router.post("/sessions/{session_id}/pause", response_model=SessionResponse)
async def pause_session(
    session_id: uuid.UUID,
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    return await _transition_session_lifecycle(session_id, "paused", current_user, db)


@router.post("/sessions/{session_id}/resume", response_model=SessionResponse)
async def resume_session(
    session_id: uuid.UUID,
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    return await _transition_session_lifecycle(session_id, "active", current_user, db)


@router.post("/sessions/{session_id}/end", response_model=SessionResponse)
async def end_session(
    session_id: uuid.UUID,
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    return await _transition_session_lifecycle(session_id, "ended", current_user, db)

@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    repo = SessionRepository(db)
    deleted = await repo.delete(session_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    return {"status": "success", "message": "Session deleted"}
