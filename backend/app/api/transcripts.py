from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
import uuid

from app.db.database import get_db
from app.db.repositories import TranscriptRepository, SessionRepository
from app.schemas.transcript import TranscriptCreate, TranscriptResponse

router = APIRouter()

@router.post("/transcripts", response_model=TranscriptResponse)
async def create_transcript_block(
    payload: TranscriptCreate,
    db: AsyncSession = Depends(get_db)
):
    try:
        session_uuid = uuid.UUID(str(payload.session_id).strip())
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session UUID format"
        )

    session_repo = SessionRepository(db)
    session = await session_repo.get_by_id(session_uuid)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    
    repo = TranscriptRepository(db)
    block = await repo.create(
        session_id=session_uuid,
        speaker=payload.speaker,
        content=payload.content,
        source=payload.source
    )
    await db.commit()
    await db.refresh(block)
    return block

@router.get("/sessions/{session_id}/transcripts", response_model=List[TranscriptResponse])
async def list_session_transcripts(
    session_id: str,
    db: AsyncSession = Depends(get_db)
):
    try:
        session_uuid = uuid.UUID(session_id) if isinstance(session_id, str) and len(session_id) == 36 else (session_id if isinstance(session_id, uuid.UUID) else None)
    except Exception:
        return []

    if not session_uuid:
        return []

    session_repo = SessionRepository(db)
    session = await session_repo.get_by_id(session_uuid)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    
    repo = TranscriptRepository(db)
    blocks = await repo.list_by_session(session_uuid)
    return blocks

