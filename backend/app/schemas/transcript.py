from pydantic import BaseModel
from typing import Optional
import uuid
from datetime import datetime

class TranscriptCreate(BaseModel):
    session_id: str
    speaker: str  # 'interviewer' or 'you'
    content: Optional[str] = ''
    text: Optional[str] = None
    source: str = "browser_audio"

    def get_content(self) -> str:
        return self.content or self.text or ""

class TranscriptResponse(BaseModel):
    id: uuid.UUID
    session_id: str
    speaker: str
    content: str
    source: str
    created_at: datetime

    class Config:
        from_attributes = True
