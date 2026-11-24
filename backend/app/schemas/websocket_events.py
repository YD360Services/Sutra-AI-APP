from typing import Optional, Any, Dict
from pydantic import BaseModel, Field
import time
import uuid

class WSEventBase(BaseModel):
    protocol_version: str = "2.0"
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event: str
    timestamp: float = Field(default_factory=time.time)

# Client -> Server Events
class SessionStartEvent(WSEventBase):
    event: str = "session.start"
    session_id: str
    user_id: Optional[str] = None
    plan: Optional[str] = "free"

class AnswerRequestEvent(WSEventBase):
    event: str = "answer.request"
    session_id: str
    question: str
    model: Optional[str] = None
    source_type: Optional[str] = "transcript"

class PredictionCancelEvent(WSEventBase):
    event: str = "prediction.cancel"
    session_id: str
    prediction_id: Optional[str] = None
    generation_version: Optional[int] = None
    reason: Optional[str] = "user_cancelled"

class HeartbeatEvent(WSEventBase):
    event: str = "heartbeat"
    timestamp: float = Field(default_factory=time.time)

# Server -> Client Events
class TranscriptInterimEvent(WSEventBase):
    event: str = "transcript.interim"
    session_id: str
    text: str
    is_final: bool = False

class TranscriptFinalEvent(WSEventBase):
    event: str = "transcript.final"
    session_id: str
    text: str
    is_final: bool = True
    speech_final: bool = True

class QuestionPredictedEvent(WSEventBase):
    event: str = "question.predicted"
    session_id: str
    prediction_id: str
    generation_version: int
    predicted_question: str
    confidence: float
    category: Optional[str] = None
    intent: Optional[str] = None

class AnswerStartedEvent(WSEventBase):
    event: str = "answer.started"
    session_id: str
    answer_id: str
    prediction_id: str
    generation_version: int
    model: str

class AnswerChunkEvent(WSEventBase):
    event: str = "answer.chunk"
    session_id: str
    answer_id: str
    prediction_id: str
    generation_version: int
    seq: int
    text: str

class AnswerCompletedEvent(WSEventBase):
    event: str = "answer.completed"
    session_id: str
    answer_id: str
    prediction_id: str
    generation_version: int
    full_answer: str
    tokens: int
    duration_ms: float

class AckEvent(WSEventBase):
    event: str = "ack"
    reply_to: Optional[str] = None
    status: str = "ok"

class ErrorEvent(WSEventBase):
    event: str = "error"
    code: str
    message: str
    session_id: Optional[str] = None
