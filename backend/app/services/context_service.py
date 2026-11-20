from sqlalchemy.ext.asyncio import AsyncSession
import uuid
from typing import Any, Dict, Optional

import json
import logging

logger = logging.getLogger("roundmate.context_service")

from app.db.repositories import SessionRepository, ResumeRepository, JDRepository, KnowledgeRepository, QARepository
from app.db.models import JobDescription, Resume, KnowledgeDocument

async def build_session_context(
    session_id: Optional[uuid.UUID],
    latest_question: str,
    db: AsyncSession,
    resume_content: Optional[str] = None,
    knowledge_content: Optional[str] = None,
    session=None,
) -> Dict[str, Any]:
    """Gather the candidate context for one answer. Returns data only; prompt wording lives in prompt_builder."""
    from app.services.transcript_intelligence import transcript_engine
    from app.services.context_orchestrator import context_orchestrator

    if session is None and session_id:
        session = await SessionRepository(db).get_by_id(session_id)
    user_id = session.user_id if session else None
    session_jd_id = session.job_description_id if session else None

    analysis = transcript_engine.analyze(latest_question)
    context = await context_orchestrator.prepare_context(
        session_id=session_id,
        user_id=user_id,
        db=db,
        keywords=analysis.get("keywords", []),
        technologies=analysis.get("technologies", []),
        resume_content=resume_content,
        knowledge_content=knowledge_content,
        session_jd_id=session_jd_id,
    )
    context["role_info"] = role_info_for(session)
    return context


def role_info_for(session) -> str:
    role = getattr(session, "role_name", None) if session else None
    company = getattr(session, "company_name", None) if session else None
    if role and company:
        return f"Interviewing for: {role} at {company}"
    if role or company:
        return f"Interviewing for: {role or company}"
    return ""
