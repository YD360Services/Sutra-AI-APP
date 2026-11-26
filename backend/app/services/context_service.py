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

    # 3. Retrieve Technical RAG Semantic Knowledge
    try:
        from app.services.rag_service import rag_service
        rag_chunks = await rag_service.retrieve_relevant_chunks(
            db=db,
            query=latest_question,
            user_id=str(user_id) if user_id else None,
            top_k=3,
            min_similarity=0.35
        )
        if rag_chunks:
            context["rag_context"] = rag_service.format_rag_context(rag_chunks)
            # If knowledge_context was default empty, replace/augment with RAG context
            if context.get("knowledge_context") in ("None loaded.", "", None):
                context["knowledge_context"] = context["rag_context"]
            else:
                context["knowledge_context"] = f"{context['knowledge_context']}\n\n{context['rag_context']}"
    except Exception as e:
        logger.debug(f"[ContextService] Technical RAG retrieval skipped: {e}")

    context["role_info"] = role_info_for(session)

    # 4. Wire LLD v1.3 Answerability, Multi-Label Classification, Pronoun Resolution & Manifest (Items 29, 30, 31, 39, 40)
    import hashlib
    conv_state = await context_orchestrator.get_conversation_state(str(session_id)) if session_id else {"entities": []}
    pronoun_res = context_orchestrator.resolve_pronoun_reference(
        latest_question, active_entities=conv_state.get("entities", [])
    )
    answerability = context_orchestrator.classify_answerability_mode(
        pronoun_res["resolved_question"],
        candidate_projects_text=context.get("resume_context", ""),
    )
    q_class = transcript_engine.classify_multi_label_question(latest_question)

    manifest = {
        "has_resume": context.get("resume_context") not in ("None loaded.", "", None),
        "has_jd": context.get("jd_context") not in ("None loaded.", "", None),
        "has_knowledge": context.get("knowledge_context") not in ("None loaded.", "", None),
        "answerability_mode": answerability["mode"],
        "primary_label": q_class["primary_label"],
    }
    fp_raw = f"{pronoun_res['resolved_question']}|{context.get('resume_context','')[:200]}|{answerability['mode']}"
    context["answerability"] = answerability
    context["question_classification"] = q_class
    context["resolved_question"] = pronoun_res["resolved_question"]
    context["source_manifest"] = manifest
    context["context_fingerprint"] = hashlib.sha256(fp_raw.encode("utf-8")).hexdigest()
    return context


def role_info_for(session) -> str:
    role = getattr(session, "role_name", None) if session else None
    company = getattr(session, "company_name", None) if session else None
    if role and company:
        return f"Interviewing for: {role} at {company}"
    if role or company:
        return f"Interviewing for: {role or company}"
    return ""
