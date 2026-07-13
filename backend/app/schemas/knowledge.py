from pydantic import BaseModel
import uuid
from datetime import datetime
from typing import Optional

class KnowledgeCreate(BaseModel):
    document_name: str
    document_type: str = "text"
    content: str

class KnowledgeResponse(BaseModel):
    id: uuid.UUID
    user_id: Optional[uuid.UUID]
    document_name: str
    document_type: str
    content: str
    uploaded_at: datetime

    class Config:
        from_attributes = True
