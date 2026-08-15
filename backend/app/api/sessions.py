from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
import uuid

from app.db.database import get_db
from app.db.repositories import SessionRepository
from app.schemas.session import SessionCreate, SessionUpdate, SessionResponse
from app.core.user_utils import normalize_user_id

router = APIRouter()

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
    db: AsyncSession = Depends(get_db)
):
    normalized_user_id = normalize_user_id(user_id)
    repo = SessionRepository(db)
    sessions = await repo.list_by_user(user_id=normalized_user_id)
    return sessions

async def generate_session_summary_if_needed(session_id: uuid.UUID, db: AsyncSession, repo: SessionRepository) -> Optional[str]:
    import logging
    logger = logging.getLogger("copilotx.sessions")
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
    db: AsyncSession = Depends(get_db)
):
    repo = SessionRepository(db)
    update_data = payload.dict(exclude_unset=True)

    session = await repo.update(session_id, **update_data)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    await db.commit()
    await db.refresh(session)

    # Generate summary & save transcripts if completing the session
    if payload.status == "completed" and not session.summary:
        generated_summary = await generate_session_summary_if_needed(session_id, db, repo)
        if generated_summary:
            session.summary = generated_summary

    return session

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
