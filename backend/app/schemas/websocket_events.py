import uuid
import time
from typing import Optional, Any, Dict
from pydantic import BaseModel, Field

class WSStandardEnvelope(BaseModel):
    """
    RoundMate LLD v1.3 Canonical Event Envelope.
    Enforces unified tracking across question revisions and generation restarts.
    """
    protocol_version: int = Field(default=1, description="LLD v1.3 protocol specification version")
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event: str = Field(..., description="Action identifier (e.g., 'answer.final.delta', 'session.start')")
    session_id: str
    question_id: Optional[str] = None
    question_version: Optional[int] = Field(default=1, description="Monotonic question interpretation version")
    prediction_id: Optional[str] = None
    generation_id: Optional[str] = None
    generation_version: Optional[int] = Field(default=1, description="Monotonic answer generation version")
    sequence: int = Field(default=0, description="Strictly increasing packet sequence per session")
    created_at: float = Field(default_factory=time.time)
    payload: Dict[str, Any] = Field(default_factory=dict)

# Backward-compatibility base class (inherits properties from WSStandardEnvelope)
class WSEventBase(WSStandardEnvelope):
    pass

# =====================================================================
# Client -> Server Events
# =====================================================================

class SessionStartEvent(WSStandardEnvelope):
    event: str = "session.start"
    user_id: Optional[str] = None
    plan: Optional[str] = "free"

class AudioFrameEvent(WSStandardEnvelope):
    event: str = "audio.frame"
    # Audio payload metadata; actual binary audio frames can be sequenced binary or base64 in payload
    sample_rate: int = 16000
    channels: int = 1
    format: str = "pcm_s16le"
    frame_ms: int = 250

class AnswerRequestEvent(WSStandardEnvelope):
    event: str = "answer.request"
    question: Optional[str] = None
    model: Optional[str] = None
    source_type: Optional[str] = "transcript"

class GenerationCancelEvent(WSStandardEnvelope):
    event: str = "generation.cancel"
    reason: Optional[str] = "user_cancelled"

# Alias for backward compatibility
PredictionCancelEvent = GenerationCancelEvent

class HeartbeatPingEvent(WSStandardEnvelope):
    event: str = "heartbeat.ping"

class HeartbeatPongEvent(WSStandardEnvelope):
    event: str = "heartbeat.pong"

# Alias for backward compatibility
class HeartbeatEvent(WSStandardEnvelope):
    event: str = "heartbeat"

# =====================================================================
# Server -> Client Events
# =====================================================================

class TranscriptInterimEvent(WSStandardEnvelope):
    event: str = "transcript.interim"
    text: Optional[str] = None
    confidence: float = 0.0
    is_final: bool = False

class TranscriptFinalEvent(WSStandardEnvelope):
    event: str = "transcript.final"
    text: Optional[str] = None
    confidence: float = 1.0
    is_final: bool = True
    speech_final: bool = True

class QuestionPredictedEvent(WSStandardEnvelope):
    event: str = "question.predicted"
    predicted_question: Optional[str] = None
    confidence: float = 0.0
    category: Optional[str] = None
    intent: Optional[str] = None

# --- Provisional Answer Lifecycle (Speculative Generation) ---

class AnswerProvisionalStartedEvent(WSStandardEnvelope):
    event: str = "answer.provisional.started"
    model: Optional[str] = None

class AnswerProvisionalDeltaEvent(WSStandardEnvelope):
    event: str = "answer.provisional.delta"
    delta_text: Optional[str] = None
    confidence: float = 0.0

class AnswerProvisionalUpdatedEvent(WSStandardEnvelope):
    event: str = "answer.provisional.updated"
    full_text: Optional[str] = None

class AnswerProvisionalCancelledEvent(WSStandardEnvelope):
    event: str = "answer.provisional.cancelled"
    reason: Optional[str] = "superseded"

# --- Final Answer Lifecycle (Canonical Candidate Delivery) ---

class AnswerFinalStartedEvent(WSStandardEnvelope):
    event: str = "answer.final.started"
    model: Optional[str] = None

class AnswerFinalDeltaEvent(WSStandardEnvelope):
    event: str = "answer.final.delta"
    delta_text: Optional[str] = None
    accumulated_length: int = 0

class AnswerFinalCompletedEvent(WSStandardEnvelope):
    event: str = "answer.final.completed"
    full_answer: Optional[str] = None
    tokens: int = 0
    duration_ms: float = 0.0

# Backward compatibility aliases
class AnswerStartedEvent(WSStandardEnvelope):
    event: str = "answer.started"
    answer_id: Optional[str] = None
    model: Optional[str] = None

class AnswerChunkEvent(WSStandardEnvelope):
    event: str = "answer.chunk"
    answer_id: Optional[str] = None
    seq: int = 0
    text: Optional[str] = None

class AnswerCompletedEvent(WSStandardEnvelope):
    event: str = "answer.completed"
    answer_id: Optional[str] = None
    full_answer: Optional[str] = None
    tokens: int = 0
    duration_ms: float = 0.0

class AckEvent(WSStandardEnvelope):
    event: str = "ack"
    reply_to: Optional[str] = None
    status: str = "ok"

class ErrorEvent(WSStandardEnvelope):
    event: str = "error"
    code: str = "UNKNOWN_ERROR"
    message: str = "An error occurred"
