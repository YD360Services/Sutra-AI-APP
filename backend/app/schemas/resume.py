from pydantic import BaseModel
import uuid
from datetime import datetime
from typing import Optional

class ResumeCreate(BaseModel):
    file_name: str
    parsed_content: str

class ResumeResponse(BaseModel):
    id: uuid.UUID
    user_id: Optional[uuid.UUID]
    file_name: str
    parsed_content: str
    is_active: bool
    uploaded_at: datetime
    introduction: Optional[str] = None
    professional_summary: Optional[str] = None
    career_journey: Optional[str] = None
    strengths: Optional[str] = None
    project_summary: Optional[str] = None

    class Config:
        from_attributes = True
