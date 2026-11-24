import uuid
from datetime import datetime
from typing import List, Optional
from sqlalchemy import String, Boolean, ForeignKey, Text, DateTime, Integer, CHAR, TypeDecorator, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.db.database import Base

class GUID(TypeDecorator):
    """Platform-independent GUID type.
    Uses PostgreSQL's UUID type, otherwise uses CHAR(36).
    """
    impl = CHAR(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(UUID(as_uuid=True))
        else:
            return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == 'postgresql':
            return value
        else:
            if isinstance(value, uuid.UUID):
                return str(value)
            try:
                return str(uuid.UUID(str(value)))
            except Exception:
                return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(str(value))
        except Exception:
            return value

class User(Base):
    __tablename__ = "users"
    
    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(String(50), default="Free")
    active_session_token: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    active_device_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_active_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    sessions: Mapped[List["Session"]] = relationship("Session", back_populates="user", cascade="all, delete-orphan")
    resumes: Mapped[List["Resume"]] = relationship("Resume", back_populates="user", cascade="all, delete-orphan")
    knowledge_documents: Mapped[List["KnowledgeDocument"]] = relationship("KnowledgeDocument", back_populates="user", cascade="all, delete-orphan")
    job_descriptions: Mapped[List["JobDescription"]] = relationship("JobDescription", back_populates="user", cascade="all, delete-orphan")
    mock_interviews: Mapped[List["MockInterview"]] = relationship("MockInterview", back_populates="user", cascade="all, delete-orphan")


class JobDescription(Base):
    __tablename__ = "job_descriptions"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped[Optional["User"]] = relationship("User", back_populates="job_descriptions")
    sessions: Mapped[List["Session"]] = relationship("Session", back_populates="job_description")


class Session(Base):
    __tablename__ = "sessions"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    session_name: Mapped[str] = mapped_column(String(255), nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role_name: Mapped[str] = mapped_column(String(255), nullable=False)
    job_description_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("job_descriptions.id", ondelete="SET NULL"), nullable=True)
    language: Mapped[str] = mapped_column(String(100), default="English")
    audio_source: Mapped[str] = mapped_column(String(100), default="browser_tab_audio")
    status: Mapped[str] = mapped_column(String(50), default="active")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


    user: Mapped[Optional["User"]] = relationship("User", back_populates="sessions")
    job_description: Mapped[Optional["JobDescription"]] = relationship("JobDescription", back_populates="sessions")
    transcript_blocks: Mapped[List["TranscriptBlock"]] = relationship("TranscriptBlock", back_populates="session", cascade="all, delete-orphan")
    question_answers: Mapped[List["QuestionAnswer"]] = relationship("QuestionAnswer", back_populates="session", cascade="all, delete-orphan")

    @property
    def ai_usage(self) -> int:
        if "question_answers" in self.__dict__:
            return len(self.question_answers) if self.question_answers else 0
        return 0



class TranscriptBlock(Base):
    __tablename__ = "transcript_blocks"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    speaker: Mapped[str] = mapped_column(String(100), nullable=False)  # 'interviewer', 'you', 'system', 'full_session'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(100), default="browser_audio")  # 'browser_audio', 'system_audio', 'mixed_audio', 'manual', 'session_end_flush'
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped["Session"] = relationship("Session", back_populates="transcript_blocks")


class QuestionAnswer(Base):
    __tablename__ = "question_answers"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(100), nullable=False)  # 'transcript', 'manual', 'screenshot'
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped["Session"] = relationship("Session", back_populates="question_answers")


class Resume(Base):
    __tablename__ = "resumes"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    parsed_content: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    introduction: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    professional_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    career_journey: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    strengths: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    project_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped[Optional["User"]] = relationship("User", back_populates="resumes")


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    document_name: Mapped[str] = mapped_column(String(255), nullable=False)
    document_type: Mapped[str] = mapped_column(String(100), default="text")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[Optional["User"]] = relationship("User", back_populates="knowledge_documents")


class MockInterview(Base):
    __tablename__ = "mock_interviews"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(255), nullable=False)
    interview_type: Mapped[str] = mapped_column(String(100), nullable=False)
    jd: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration: Mapped[str] = mapped_column(String(100), nullable=False)
    history_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[Optional["User"]] = relationship("User", back_populates="mock_interviews")


class AdminEmail(Base):
    __tablename__ = "admin_emails"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    user_email: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    type: Mapped[str] = mapped_column(String(50), default="info")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    duration: Mapped[int] = mapped_column(Integer, default=4500)
    target_screen: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    target_tab: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    user_name: Mapped[str] = mapped_column(String(255), default="Anonymous")
    user_photo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), default="General")
    status: Mapped[str] = mapped_column(String(50), default="Open")
    priority: Mapped[str] = mapped_column(String(50), default="Medium")
    created_time: Mapped[str] = mapped_column(String(100), default="Recently")
    messages_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AITokenUsage(Base):
    __tablename__ = "ai_token_usage"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    endpoint: Mapped[str] = mapped_column(String(100), default="/api/answer/stream")
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    trace_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    generation_id: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class PlatformHealthCheck(Base):
    """Latest manually recorded check for one supported interview platform (one row per platform).

    Written only by admins (see app/api/platform_health.py). A platform with no row is
    "not reported"; nothing here is inferred or defaulted.
    """
    __tablename__ = "platform_health_checks"

    platform_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    troubleshooting: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SemanticEmbedding(Base):
    """
    High-dimensional vector embedding record supporting pgvector semantic retrieval
    for candidate resumes, technical knowledge documents, and interview question banks.
    """
    __tablename__ = "semantic_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    source_type: Mapped[str] = mapped_column(String(100), index=True, nullable=False)  # 'resume', 'knowledge_document', 'interview_bank', 'session_qa'
    source_id: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    tsv_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True, index=True)  # Pre-computed tokens for PostgreSQL GIN / SQLite FTS
    embedding_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON-encoded float array (768-dim)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


# =====================================================================
# LLD v1.3 Core Lifecycle, Evidence & Provenance Entities
# =====================================================================

class TranscriptSegment(Base):
    """Immutable raw ASR transcript segment paired with derived normalized/corrected text."""
    __tablename__ = "transcript_segments"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    speaker: Mapped[str] = mapped_column(String(100), default="interviewer")
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)
    speech_final: Mapped[bool] = mapped_column(Boolean, default=False)
    processor_version: Mapped[str] = mapped_column(String(50), default="v1.3")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ConversationTurn(Base):
    """Durable dialogue turn linking question interpretation, answer generation, and timings."""
    __tablename__ = "conversation_turns"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    turn_index: Mapped[int] = mapped_column(Integer, default=0)
    question_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    final_question: Mapped[str] = mapped_column(Text, nullable=False)
    final_answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    generation_id: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    topic: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    intent: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class QuestionEvent(Base):
    """Tracks question interpretation lifecycle and monotonic version transitions."""
    __tablename__ = "question_events"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    question_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    question_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(50), default="predicted")  # predicted, revised, finalized, superseded
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class QuestionPrediction(Base):
    """Stores interim speculative question predictions and promotion fingerprint metadata."""
    __tablename__ = "question_predictions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    prediction_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    session_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    question_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    predicted_question: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    fingerprint: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    was_promoted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AnswerGeneration(Base):
    """Tracks provisional and final LLM answer generations, supersession, and context fingerprints."""
    __tablename__ = "answer_generations"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    generation_id: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    session_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    question_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    question_version: Mapped[int] = mapped_column(Integer, default=1)
    generation_version: Mapped[int] = mapped_column(Integer, default=1)
    is_provisional: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(50), default="started")  # started, completed, cancelled, superseded, failed
    provider: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    model_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    context_fingerprint: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    superseded_by_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    answer_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class CandidateProfile(Base):
    """Structured verified candidate profile separate from raw uploaded resume text."""
    __tablename__ = "candidate_profiles"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    profile_version: Mapped[int] = mapped_column(Integer, default=1)
    skills_json: Mapped[str] = mapped_column(Text, default="[]")
    verified_facts_json: Mapped[str] = mapped_column(Text, default="[]")
    verification_status: Mapped[str] = mapped_column(String(50), default="imported")  # imported, user_confirmed
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CandidateProject(Base):
    """Verified candidate project ownership and technical evidence for truthfulness guard."""
    __tablename__ = "candidate_projects"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_profiles.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    project_name: Mapped[str] = mapped_column(String(255), nullable=False)
    ownership_role: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence_source: Mapped[str] = mapped_column(String(100), default="resume")  # resume, user_confirmed, session_statement
    tech_stack_json: Mapped[str] = mapped_column(Text, default="[]")
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SourceProvenance(Base):
    """Tracks evidence source origin, tenant/ACL scope, and permissions for RAG chunks."""
    __tablename__ = "source_provenance"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    document_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    access_scope: Mapped[str] = mapped_column(String(50), default="private")  # private, tenant, shared_opt_in
    document_version: Mapped[int] = mapped_column(Integer, default=1)
    checksum: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    storage_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # S3/R2 object key
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LLMModelRegistryRecord(Base):
    """Persisted LLM model capabilities, pricing, and availability configuration."""
    __tablename__ = "llm_model_registry"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    model_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    supports_streaming: Mapped[bool] = mapped_column(Boolean, default=True)
    context_window: Mapped[int] = mapped_column(Integer, default=128000)
    input_cost_per_1m: Mapped[float] = mapped_column(Float, default=0.0)
    output_cost_per_1m: Mapped[float] = mapped_column(Float, default=0.0)
    avg_ttft_ms: Mapped[int] = mapped_column(Integer, default=500)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

