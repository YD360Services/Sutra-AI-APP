from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
import uuid

from app.db.database import get_db
from app.db.repositories import SessionRepository
from app.schemas.session import SessionCreate, SessionUpdate, SessionResponse

router = APIRouter()

@router.post("/sessions", response_model=SessionResponse)
async def start_session(
    payload: SessionCreate,
    user_id: Optional[uuid.UUID] = None,  # Mock user auth dependency in demo
    db: AsyncSession = Depends(get_db)
):
    repo = SessionRepository(db)
    
    session_name = payload.session_name or f"Mock Prep Session with {payload.company_name}"
    
    session = await repo.create(
        session_name=session_name,
        company_name=payload.company_name,
        role_name=payload.role_name,
        language=payload.language,
        audio_source=payload.audio_source,
        user_id=user_id,
        job_description_id=payload.job_description_id
    )
    await db.commit()
    await db.refresh(session)
    return session

@router.get("/sessions", response_model=List[SessionResponse])
async def list_sessions(
    user_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db)
):
    repo = SessionRepository(db)
    sessions = await repo.list_by_user(user_id=user_id)
    return sessions

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
    return session

@router.patch("/sessions/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: uuid.UUID,
    payload: SessionUpdate,
    db: AsyncSession = Depends(get_db)
):
    repo = SessionRepository(db)
    update_data = payload.dict(exclude_unset=True)
    
    # Generate summary & save transcripts if completing the session
    if payload.status == "completed":
        try:
            from app.cache.redis import redis_cache
            from app.db.repositories import TranscriptRepository
            
            # 1. Retrieve full transcript text from Redis cache
            redis_transcript = await redis_cache.get_transcript(str(session_id))
            if redis_transcript and redis_transcript.strip():
                # 2. Save the transcript as a block in the PostgreSQL database
                t_repo = TranscriptRepository(db)
                await t_repo.create(
                    session_id=session_id,
                    speaker="interview",
                    content=redis_transcript,
                    source="websocket"
                )
                
                # 3. Generate a session summary with the LLM API
                try:
                    from app.services.ai_service import call_llm
                    db_session = await repo.get_by_id(session_id)
                    company = db_session.company_name if db_session else "Unknown"
                    role = db_session.role_name if db_session else "Unknown"
                    
                    summary_prompt = f"""
Analyze the following transcript of an interview prep session for a {role} position at {company}.
Create a professional, highly readable summary of the session.
Focus on:
- Key questions and topics discussed (e.g. databases, system design, coding questions).
- Technologies mentioned.
- Areas of strength and areas needing improvement.

Transcript:
{redis_transcript}
"""
                    system_prompt = "You are an expert technical interviewer. Generate a concise, clear, bulleted summary of the interview prep session."
                    summary_text = await call_llm(
                        prompt=summary_prompt,
                        system_prompt=system_prompt,
                        temperature=0.3
                    )
                    if summary_text:
                        update_data["summary"] = summary_text
                except Exception as ai_err:
                    import logging
                    logging.getLogger("copilotx.sessions").warning(f"Failed to generate AI session summary: {ai_err}")
        except Exception as e:
            import logging
            logging.getLogger("copilotx.sessions").error(f"Failed during session completion pipeline: {e}")

    session = await repo.update(session_id, **update_data)
    if not session:
      raise HTTPException(
          status_code=status.HTTP_404_NOT_FOUND,
          detail="Session not found"
      )
    await db.commit()
    await db.refresh(session)
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
