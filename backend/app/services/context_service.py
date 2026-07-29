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

USER RESUME/BACKGROUND:
{context.get("resume_context", "None loaded.")}

KNOWLEDGE DOCUMENTS / REFERENCE MATERIAL:
{context.get("knowledge_context", "None loaded.")}

RECENT SESSION Q&A HISTORY:
{context.get("previous_context", "None.")}

LATEST QUESTION TO ANSWER:
{latest_question}

SYSTEM MANDATE & INSTRUCTIONS:
- You are an expert candidate in an active job interview.
- CRITICAL: ALIGN AND SYNC YOUR ANSWER DIRECTLY TO THE JOB DESCRIPTION (JD) AND RESUME CONTEXT ABOVE.
- If asked to introduce yourself ("tell me about yourself", "walk me through your resume", "introduce yourself", "tell me about your background"), renovate and adapt your self-introduction so that your background, experience, accomplishments, and skills directly match and highlight the key requirements, technologies, and responsibilities in the Job Description (JD).
- Speak in plain, natural English — no bullet points, no asterisks, no numbered lists, no markdown of any kind. No headers, no structured formatting. Just talk naturally.
- Keep it concise: 1 or 2 short paragraphs max. Get to the point quickly.
""".strip()

    return context_prompt
