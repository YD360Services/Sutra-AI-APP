from sqlalchemy.ext.asyncio import AsyncSession
import uuid
from typing import Optional

import json
import logging

logger = logging.getLogger("copilotx.context_service")

from app.db.repositories import SessionRepository, ResumeRepository, JDRepository, KnowledgeRepository, QARepository
from app.db.models import JobDescription, Resume, KnowledgeDocument

async def build_session_context(
    session_id: Optional[uuid.UUID],
    latest_question: str,
    db: AsyncSession,
    resume_content: Optional[str] = None,
    knowledge_content: Optional[str] = None
) -> str:
    from app.services.transcript_intelligence import transcript_engine
    from app.services.context_orchestrator import context_orchestrator
    from app.db.repositories import SessionRepository

    session_repo = SessionRepository(db)
    session = await session_repo.get_by_id(session_id) if session_id else None
    user_id = session.user_id if session else None
    session_jd_id = session.job_description_id if session else None

    # Sync analysis of the latest question for keywords and technologies
    analysis = transcript_engine.analyze(latest_question)
    keywords = analysis.get("keywords", [])
    technologies = analysis.get("technologies", [])

    context = await context_orchestrator.prepare_context(
        session_id=session_id,
        user_id=user_id,
        db=db,
        keywords=keywords,
        technologies=technologies,
        resume_content=resume_content,
        knowledge_content=knowledge_content,
        session_jd_id=session_jd_id
    )

    role_info = f"Role: {session.role_name if session else 'Software Engineer'} at {session.company_name if session else 'Target Company'}"

    context_prompt = f"""
ROLE & COMPANY:
{role_info}

JOB DESCRIPTION:
{context.get("jd_context", "None loaded.")}

USER RESUME / BACKGROUND:
{context.get("resume_context", "None loaded.")}

CANDIDATE MOCK PRACTICE ANSWERS & REFERENCE MATERIAL (FOR THIS ROLE):
{context.get("knowledge_context", "None loaded.")}

RECENT SESSION Q&A HISTORY:
{context.get("previous_context", "None.")}

LATEST QUESTION TO ANSWER:
{latest_question}

SYSTEM MANDATE & INSTRUCTIONS:
- You ARE the human candidate sitting in the interview right now. Speak in the FIRST PERSON ("I", "my experience", "I've built", "in my previous team").
- MANDATE: DELIVER YOUR ANSWER EXACTLY LIKE A HIGH-SCORING MOCK INTERVIEW MODEL RESPONSE.
- REUSE AND ALIGN WITH THE CANDIDATE'S PREPARED MOCK INTERVIEW PRACTICE ANSWERS AND STAR STRUCTURE FOR THIS ROLE SHOWN ABOVE.
- If asked a question that relates to past mock interview practice, respond naturally using the candidate's prepared points, personal metrics, and STAR framework.
- Talk like a real, confident human candidate out loud — direct, articulate, concise, using spoken contractions: I've, I'd, I'm, that's.
""".strip()

    return context_prompt
