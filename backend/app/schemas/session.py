from pydantic import BaseModel
from typing import Optional
import uuid
from datetime import datetime

class SessionCreate(BaseModel):
    session_name: Optional[str] = None
    company_name: str
    role_name: str
    job_description_id: Optional[uuid.UUID] = None
    language: Optional[str] = "English"
    audio_source: Optional[str] = "browser_tab_audio"

class SessionUpdate(BaseModel):
    status: Optional[str] = None
    session_name: Optional[str] = None
    summary: Optional[str] = None
    duration_seconds: Optional[int] = None

class SessionResponse(BaseModel):
    id: uuid.UUID
    user_id: Optional[uuid.UUID]
    session_name: str
    company_name: str
    role_name: str
    job_description_id: Optional[uuid.UUID]
    language: str
    audio_source: str
    status: str
    started_at: datetime
    ended_at: Optional[datetime]
    duration_seconds: int
    ai_usage: int = 0
    summary: Optional[str] = None
    created_at: datetime
    updated_at: datetime



    class Config:
        from_attributes = True
