from pydantic import BaseModel
import uuid
from datetime import datetime
from typing import Optional, List, Any

class AnswerRequest(BaseModel):
    session_id: Optional[str] = None
    question: Optional[str] = None
    transcript: Optional[str] = None
    qa_history: Optional[List[Any]] = None
    source_type: str = "transcript"  # 'transcript', 'manual', 'screenshot'
    resume_content: Optional[str] = None
    knowledge_content: Optional[str] = None
    model: Optional[str] = None

class AnswerResponse(BaseModel):
    id: uuid.UUID
    session_id: Optional[uuid.UUID | str] = None
    question: str
    answer: str
    source_type: str
    created_at: datetime

    class Config:
        from_attributes = True
