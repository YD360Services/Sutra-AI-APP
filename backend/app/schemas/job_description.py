from pydantic import BaseModel
import uuid
from datetime import datetime
from typing import Optional

class JDCreate(BaseModel):
    company_name: str
    role_name: str
    description: str

class JDResponse(BaseModel):
    id: uuid.UUID
    user_id: Optional[uuid.UUID]
    company_name: str
    role_name: str
    description: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
