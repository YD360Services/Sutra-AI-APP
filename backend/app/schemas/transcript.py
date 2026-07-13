from pydantic import BaseModel
import uuid
from datetime import datetime

class TranscriptCreate(BaseModel):
    session_id: str
    speaker: str  # 'interviewer' or 'you'
    content: str
    source: str = "browser_audio"

class TranscriptResponse(BaseModel):
    id: uuid.UUID
    session_id: str
    speaker: str
    content: str
    source: str
    created_at: datetime

    class Config:
        from_attributes = True
