from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
import json
import logging
import uuid
import os
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel

from app.db.database import get_db
from app.db.repositories import QARepository, SessionRepository
from app.schemas.answer import AnswerRequest, AnswerResponse
from app.services.context_service import build_session_context, role_info_for
from app.services.context_orchestrator import context_orchestrator
from app.services.ai_service import call_gemini, call_llm, stream_llm, resolve_model_by_task
from app.services.prompt_builder import (
    build_system_prompt,
    build_context_message,
    build_introduction_message,
    resolve_mode,
    session_type_from_name,
)
from app.cache.redis import redis_cache
from app.core.rate_limiter import rate_limit

router = APIRouter()
logger = logging.getLogger("roundmate.answers")

ENABLE_PROMPT_LOGGING = os.getenv("ENABLE_PROMPT_LOGGING", "false").lower() in ("true", "1")

def _log_prompt_to_file(question: str, system_prompt: str, user_prompt: str, prompt_type: str, source_type: str):
    """Write the exact system + user prompt sent to the LLM asynchronously to eliminate disk I/O from the hot path."""
    if not ENABLE_PROMPT_LOGGING:
        return

    def _write():
        try:
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            log_dir = os.path.join(base_dir, "logs", "prompt_debug")
            os.makedirs(log_dir, exist_ok=True)
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
            fname = os.path.join(log_dir, f"{ts}_{source_type}_{prompt_type}.txt")
            with open(fname, "w", encoding="utf-8") as f:
                f.write(f"=== PROMPT DEBUG LOG ===\n")
                f.write(f"Timestamp     : {datetime.utcnow().isoformat()}\n")
                f.write(f"Source Type   : {source_type}\n")
                f.write(f"Prompt Type   : {prompt_type}\n")
                f.write(f"Question      : {question}\n")
                f.write(f"\n{'='*60}\nSYSTEM PROMPT :\n{'='*60}\n{system_prompt or '(empty)'}\n")
                f.write(f"\n{'='*60}\nUSER / CONTEXT PROMPT :\n{'='*60}\n{user_prompt or '(empty)'}\n")
        except Exception as e:
            logger.debug(f"[PromptDebug] Failed to write prompt log: {e}")

    import threading
    threading.Thread(target=_write, daemon=True).start()

def extract_question_from_transcript(transcript: str) -> str:
    """Extract the most recent meaningful question from a raw transcript, preserving multi-part questions."""
    import re
    if not transcript or len(transcript.strip()) < 5:
        return transcript
    
    clean_t = transcript.strip()
    # Strip trailing conversational filler / acknowledgments (e.g. "Okay.", "Right.", "Got it.")
    clean_t = re.sub(r'(?i)(?<=[.?!])\s+(?:okay|ok|yeah|yep|sure|right|got it|cool|fine|all right)[.?!]?\s*$', '', clean_t).strip()

    # If the transcript is relatively short (under 400 chars), return the whole segment
    if len(clean_t) < 400:
        return clean_t

    # Split on sentence-ending punctuation
    sentences = re.split(r'(?<=[.?!])\s+', clean_t)
    sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 5]
    if not sentences:
        return clean_t

    # Filter out trailing filler sentences from end of list
    filler_re = re.compile(r'^(?:okay|ok|yeah|yep|sure|right|got it|cool|fine|all right)[.?!]?$', re.IGNORECASE)
    while sentences and filler_re.match(sentences[-1]):
        sentences.pop()

    if not sentences:
        return clean_t

    question_words = [
        'what', 'how', 'why', 'when', 'where', 'which', 'who',
        'can you', 'could you', 'tell me', 'explain', 'describe',
        'walk me', 'have you', 'do you', 'did you', 'are you', 'would you',
        'polish', 'improve', 'optimize', 'rewrite', 'please',
        'design', 'implement', 'build', 'write', 'create'
    ]

    # Collect question sentences working backwards from the end
    question_sentences = []
    for s in reversed(sentences[-5:]):
        s_lower = s.lower()
        is_q = s.endswith('?') or any(qw in s_lower for qw in question_words)
        if is_q or (question_sentences and len(question_sentences) < 3):
            question_sentences.insert(0, s)
        elif question_sentences:
            break

    if question_sentences:
        return ' '.join(question_sentences)

    return ' '.join(sentences[-2:]) if len(sentences) >= 2 else sentences[-1]


class _AnswerContext:
    """Prepared context bundle returned by _prepare_answer_context()."""
    __slots__ = (
        "session", "session_uuid", "latest_question", "mode",
        "custom_prompts", "context_message", "stored_introduction",
    )

    def __init__(self, session, session_uuid, latest_question, mode,
                 custom_prompts, context_message, stored_introduction):
        self.session = session
        self.session_uuid = session_uuid
        self.latest_question = latest_question
        self.mode = mode
        self.custom_prompts = custom_prompts
        self.context_message = context_message
        self.stored_introduction = stored_introduction

    def system_prompt(self, output: str) -> str:
        return build_system_prompt(self.mode, self.custom_prompts, output=output)


def _format_client_qa_history(qa_history) -> str:
    entries = []
    if isinstance(qa_history, list):
        for item in qa_history:
            if not isinstance(item, dict) or not item.get("question"):
                continue
            q_txt = str(item.get("question", "")).strip()
            a_txt = str(item.get("answer", "")).strip()
            if a_txt:
                a_snippet = a_txt[:300] + "..." if len(a_txt) > 300 else a_txt
                entries.append(f"Q: {q_txt}\nA: {a_snippet}")
            else:
                entries.append(f"Q: {q_txt}")
    return "\n\n".join(entries)


async def _prepare_answer_context(
    payload: "AnswerRequest",
    db: AsyncSession,
) -> _AnswerContext:
    """
    Shared by /answer and /answer/stream: resolves the session, extracts the question,
    picks the answer mode, gathers context + the user's custom prompts, and builds the
    user message. The system prompt is built by the caller (JSON vs plain-text output).
    """
    # 1. Resolve session
    session = None
    session_uuid = None
    if payload.session_id:
        try:
            session_uuid = (
                uuid.UUID(payload.session_id)
                if isinstance(payload.session_id, str) and len(payload.session_id) == 36
                else (payload.session_id if isinstance(payload.session_id, uuid.UUID) else None)
            )
            if session_uuid:
                session = await SessionRepository(db).get_by_id(session_uuid)
        except Exception:
            pass

    # 2. Extract question
    raw_transcript = payload.transcript or ""
    if payload.question:
        latest_question = payload.question
    elif raw_transcript:
        latest_question = extract_question_from_transcript(raw_transcript)
    else:
        latest_question = ""

    # 3. Answer mode: the session type the user picked wins, then the question itself
    if payload.source_type == "screenshot":
        mode = "screenshot"
    else:
        mode = resolve_mode(latest_question, session_type_from_name(getattr(session, "session_name", "")))

    # 4. Context: prewarmed packet from speculative retrieval (0ms DB) or build it now
    context = None
    if payload.session_id:
        try:
            cached_session = await redis_cache.get_session_state(str(payload.session_id))
            pw = cached_session.get("prewarmed_context") if cached_session else None
            if isinstance(pw, dict):
                context = {
                    "role_info": role_info_for(session) or pw.get("role_info", ""),
                    "jd_context": pw.get("jd_context", ""),
                    "resume_context": pw.get("resume_context", ""),
                    "knowledge_context": pw.get("candidate_memories", ""),
                    "previous_context": pw.get("previous_context", ""),
                }
                logger.info(f"[FastPath] Using prewarmed context for session {payload.session_id}")
        except Exception as e:
            logger.warning(f"Error reading prewarmed context from Redis: {e}")

    if context is None:
        context = await build_session_context(
            session_id=session_uuid,
            latest_question=latest_question,
            db=db,
            resume_content=payload.resume_content,
            knowledge_content=payload.knowledge_content,
            session=session,
        )

    # 5. Custom prompts the user selected — these override the default answer style
    if "custom_prompts" in context:
        custom_prompts = context["custom_prompts"]
    else:
        custom_prompts = await context_orchestrator.get_custom_prompts(payload.knowledge_content, db)

    # 6. Stored self-introduction (+ non-blocking self-heal of missing resume summaries)
    stored_introduction = None
    resume_obj = None
    if payload.resume_content and len(payload.resume_content) < 100:
        try:
            from app.db.models import Resume
            resume_obj = await db.get(Resume, uuid.UUID(payload.resume_content))
        except Exception:
            pass
    if not resume_obj and session and session.user_id:
        try:
            from app.db.repositories import ResumeRepository
            resume_obj = await ResumeRepository(db).get_active(session.user_id)
        except Exception:
            pass

    if resume_obj and not resume_obj.introduction:
        async def _async_heal_resume(r_id, r_content):
            try:
                from app.services.ai_service import generate_resume_summaries
                from app.db.database import SessionLocal
                from app.db.models import Resume
                summaries = await generate_resume_summaries(r_content)
                async with SessionLocal() as db_heal:
                    r = await db_heal.get(Resume, r_id)
                    if r:
                        r.introduction = summaries.get("introduction")
                        r.professional_summary = summaries.get("professional_summary")
                        r.career_journey = summaries.get("career_journey")
                        r.strengths = summaries.get("strengths")
                        r.project_summary = summaries.get("project_summary")
                        await db_heal.commit()
                        logger.info(f"[Self-Healing] Async summaries generated and saved for resume: {r_id}")
            except Exception as she:
                logger.warning(f"[Self-Healing] Failed to generate resume summaries: {she}")

        import asyncio
        asyncio.create_task(_async_heal_resume(resume_obj.id, resume_obj.parsed_content))

    if resume_obj and resume_obj.introduction and mode != "screenshot":
        q_clean = latest_question.lower().strip().replace("?", "").replace(".", "").replace(",", "")
        intro_triggers = [
            "tell me about yourself", "introduce yourself",
            "walk me through your resume", "walk me through your background",
            "talk about yourself", "who are you",
        ]
        if any(t in q_clean for t in intro_triggers):
            stored_introduction = resume_obj.introduction
            logger.info("Found stored introduction for question: " + latest_question)

    # 7. User message: data only, no instructions
    if stored_introduction:
        mode = "introduction"
        context_message = build_introduction_message(stored_introduction)
    else:
        history = _format_client_qa_history(getattr(payload, "qa_history", None)) or context.get("previous_context", "")
        context_message = build_context_message(
            question=latest_question,
            role_info=context.get("role_info", ""),
            jd_context=context.get("jd_context", ""),
            resume_context=context.get("resume_context", ""),
            knowledge_context=context.get("knowledge_context", ""),
            history=history,
            transcript=raw_transcript,
        )

    return _AnswerContext(
        session=session,
        session_uuid=session_uuid,
        latest_question=latest_question,
        mode=mode,
        custom_prompts=custom_prompts,
        context_message=context_message,
        stored_introduction=stored_introduction,
    )


@router.post("/answer", response_model=AnswerResponse)
async def generate_answer(
    payload: AnswerRequest,
    db: AsyncSession = Depends(get_db)
):
    ctx = await _prepare_answer_context(payload, db)
    sys_prompt = ctx.system_prompt(output="json")

    # Log prompt
    _source = (
        payload.source_type
        if payload.source_type in ("manual", "transcript", "screenshot")
        else ("manual" if payload.question else "transcript")
    )
    _log_prompt_to_file(
        question=ctx.latest_question,
        system_prompt=sys_prompt,
        user_prompt=ctx.context_message,
        prompt_type=ctx.mode,
        source_type=_source,
    )

    # Call LLM
    raw_response = await call_gemini(
        prompt=ctx.context_message,
        system_prompt=sys_prompt,
        response_json=True,
        model=payload.model,
    )

    # Parse response
    question = ctx.latest_question
    answer = raw_response
    try:
        cleaned = raw_response.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        start_idx = cleaned.find('{')
        end_idx   = cleaned.rfind('}')
        if start_idx != -1 and end_idx != -1:
            cleaned = cleaned[start_idx:end_idx + 1]
        data = json.loads(cleaned, strict=False)
        ai_question = (data.get("question") or "").strip()
        # Preserve full question: never allow an LLM-shortened fragment to overwrite verbatim question
        if not ctx.latest_question or len(ctx.latest_question.strip()) < 10:
            question = ai_question or ctx.latest_question or "Interview Question"
        else:
            question = ctx.latest_question
        answer   = data.get("answer",   raw_response).strip()
    except Exception as e:
        logger.warning(f"Failed to parse Gemini response as JSON: {e}. Raw: {raw_response}")

    # Save to DB and update Redis previous_context
    if ctx.session and ctx.session_uuid:
        qa_repo = QARepository(db)
        qa = await qa_repo.create(
            session_id=ctx.session_uuid,
            question=question,
            answer=answer,
            source_type=payload.source_type,
        )
        try:
            cached_session = await redis_cache.get_session_state(str(payload.session_id))
            if cached_session:
                prev_ctx = cached_session.get("previous_context", "")
                answer_snippet = answer[:150] + "..." if len(answer) > 150 else answer
                new_entry = f"Q: {question}\nA: {answer_snippet}"
                if prev_ctx and prev_ctx != "None.":
                    parts = [p.strip() for p in prev_ctx.split("Q: ") if p.strip()]
                    parts.append(f"{question}\nA: {answer_snippet}")
                    cached_session["previous_context"] = "\n".join(
                        [f"Q: {p}" for p in parts[-2:]]
                    )
                else:
                    cached_session["previous_context"] = new_entry
                await redis_cache.set_session_state(str(payload.session_id), cached_session)
        except Exception as e:
            logger.warning(f"Failed to update previous_context in Redis cache: {e}")
        return qa
    else:
        import datetime as _dt
        return {
            "id":           uuid.uuid4(),
            "session_id":   None,
            "question":     question,
            "answer":       answer,
            "source_type":  payload.source_type,
            "created_at":   _dt.datetime.utcnow(),
        }


@router.post("/answer/stream", dependencies=[Depends(rate_limit(max_requests=120, window_seconds=60, key_prefix="stream"))])
async def generate_answer_stream(
    payload: AnswerRequest,
    db: AsyncSession = Depends(get_db)
):
    ctx = await _prepare_answer_context(payload, db)
    # Screenshots keep the JSON contract (the client parses it); everything else streams plain text.
    sys_prompt = ctx.system_prompt(output="json" if ctx.mode == "screenshot" else "text")
    # Same resolution stream_llm does internally — used for the token-cost ledger below.
    resolved_model = resolve_model_by_task(payload.model, sys_prompt)

    _source_stream = (
        payload.source_type
        if payload.source_type in ("manual", "transcript", "screenshot")
        else ("manual" if payload.question else "transcript")
    )
    _log_prompt_to_file(
        question=ctx.latest_question,
        system_prompt=sys_prompt,
        user_prompt=ctx.context_message,
        prompt_type=ctx.mode,
        source_type=_source_stream,
    )

    async def stream_generator():
        accumulated_chunks = []
        async for chunk in stream_llm(
            prompt=ctx.context_message,
            system_prompt=sys_prompt,
            model=payload.model,
            response_json=False,  # plain-text stream (sub-1s TTFT)
        ):
            accumulated_chunks.append(chunk)
            yield chunk

        # Once stream finishes, parse and save to DB
        full_response = "".join(accumulated_chunks)
        question = ctx.latest_question
        answer = full_response

        # Handle JSON fallback (backwards compatibility)
        if full_response.strip().startswith("{"):
            try:
                cleaned = full_response.strip()
                if cleaned.startswith("```json"):
                    cleaned = cleaned[7:]
                elif cleaned.startswith("```"):
                    cleaned = cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()
                start_idx = cleaned.find('{')
                end_idx   = cleaned.rfind('}')
                if start_idx != -1 and end_idx != -1:
                    cleaned = cleaned[start_idx:end_idx + 1]
                data = json.loads(cleaned, strict=False)
                ai_question = (data.get("question") or "").strip()
                # Preserve full question: never allow an LLM-shortened fragment to overwrite verbatim question
                if not ctx.latest_question or len(ctx.latest_question.strip()) < 10:
                    question = ai_question or ctx.latest_question or "Interview Question"
                else:
                    question = ctx.latest_question
                answer   = data.get("answer", full_response).strip()
            except Exception as e:
                logger.warning(f"Failed to parse stream response as JSON: {e}. Raw: {full_response}")

        if ctx.session and ctx.session_uuid:
            try:
                from app.db.database import SessionLocal
                async with SessionLocal() as db_session:
                    qa_repo = QARepository(db_session)
                    await qa_repo.create(
                        session_id=ctx.session_uuid,
                        question=question,
                        answer=answer,
                        source_type=payload.source_type,
                    )
                    await db_session.commit()

                    # Record AI token usage & estimated cost ledger
                    try:
                        p_len = len(sys_prompt) + len(ctx.context_message)
                        p_tok = max(1, p_len // 4)
                        c_tok = max(1, len(answer) // 4)
                        cost = 0.0
                        m_low = (resolved_model or "gemini").lower()
                        if "flash" in m_low: cost = (p_tok * 0.075 + c_tok * 0.30) / 1_000_000
                        elif "4o-mini" in m_low or "luna" in m_low or "sol" in m_low: cost = (p_tok * 0.15 + c_tok * 0.60) / 1_000_000
                        elif "opus" in m_low or "haiku" in m_low: cost = (p_tok * 0.25 + c_tok * 1.25) / 1_000_000
                        elif "fable" in m_low or "sonnet" in m_low: cost = (p_tok * 3.00 + c_tok * 15.00) / 1_000_000
                        elif "astra" in m_low or "terra" in m_low or "gpt-4o" in m_low: cost = (p_tok * 2.50 + c_tok * 10.00) / 1_000_000
                        else: cost = (p_tok * 0.10 + c_tok * 0.30) / 1_000_000

                        from app.db.models import AITokenUsage
                        from app.core.context import get_trace_id, get_user_id
                        u_id = get_user_id()
                        t_id = get_trace_id()
                        token_record = AITokenUsage(
                            user_id=str(u_id) if u_id else (str(ctx.session.user_id) if ctx.session and ctx.session.user_id else None),
                            session_id=str(ctx.session_uuid) if ctx.session_uuid else None,
                            endpoint="/api/answer/stream",
                            model=resolved_model or "unknown",
                            provider="gemini" if "gemini" in m_low else ("anthropic" if "claude" in m_low or "fable" in m_low else "openai"),
                            prompt_tokens=p_tok,
                            completion_tokens=c_tok,
                            total_tokens=p_tok + c_tok,
                            estimated_cost_usd=round(cost, 6),
                            duration_ms=0,
                            trace_id=t_id
                        )
                        db_session.add(token_record)
                        await db_session.commit()
                    except Exception as t_err:
                        logger.debug(f"Failed to record token usage in stream: {t_err}")
                    try:
                        cached_session = await redis_cache.get_session_state(
                            str(payload.session_id)
                        )
                        if cached_session:
                            prev_ctx = cached_session.get("previous_context", "")
                            answer_snippet = answer[:150] + "..." if len(answer) > 150 else answer
                            new_entry = f"Q: {question}\nA: {answer_snippet}"
                            if prev_ctx and prev_ctx != "None.":
                                parts = [
                                    p.strip() for p in prev_ctx.split("Q: ") if p.strip()
                                ]
                                parts.append(f"{question}\nA: {answer_snippet}")
                                cached_session["previous_context"] = "\n".join(
                                    [f"Q: {p}" for p in parts[-2:]]
                                )
                            else:
                                cached_session["previous_context"] = new_entry
                            await redis_cache.set_session_state(
                                str(payload.session_id), cached_session
                            )
                    except Exception as e:
                        logger.warning(
                            f"Failed to update previous_context in Redis cache inside "
                            f"stream generator: {e}"
                        )
            except Exception as db_err:
                logger.error(f"Failed to save QA record in streaming endpoint: {db_err}")

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

@router.get("/sessions/{session_id}/answers", response_model=List[AnswerResponse])
async def list_session_answers(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    session_repo = SessionRepository(db)
    session = await session_repo.get_by_id(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    
    repo = QARepository(db)
    qas = await repo.list_by_session(session_id)
    return qas


class ScoreResumeRequest(BaseModel):
    resume_content: Optional[str] = None
    jd_content: Optional[str] = None
    role: Optional[str] = None
    company: Optional[str] = None
    model: Optional[str] = None
    transcript: Optional[str] = None

@router.post("/answers/transcript")
async def score_resume_transcript(payload: ScoreResumeRequest):
    resume_text = payload.resume_content or ""
    jd_text = payload.jd_content or ""
    role = payload.role or "Software Engineer"
    company = payload.company or "Target Company"

    from datetime import datetime
    now = datetime.utcnow()
    current_date_str = now.strftime("%B %d, %Y")
    current_year = now.year

    if payload.transcript and not (resume_text or jd_text):
        prompt = payload.transcript
    else:
        if not jd_text:
            jd_info = f"Target Role: {role} at {company}"
        else:
            jd_info = f"Job Description:\n{jd_text}"

        prompt = f"""
Analyze the candidate's Resume against the Target Job/Role details.
Rate the suitability and match strength from 0 to 100 based on specific criteria.

Rules for Scoring:
- Compare the technical stack, programming languages, frameworks, databases, and tools in the candidate's resume to the job description.
- Calculate the percentage of required skills and years of experience that the candidate possesses.
- Award points based on role relevancy and past projects matching the target job description.
- The current date is {current_date_str} (Year: {current_year}). Calculate all years of experience relative to this date.
- Do NOT penalize the candidate or lower the score because a job has ended in the past, or because its dates are below the current date/month/year. All historical experience listed counts fully towards their experience match.
- If the candidate meets the core technical requirements (e.g., Java, Spring Boot, etc. for a Java Developer role), grade them highly.
- You must respond with ONLY a single integer score between 0 and 100 based on your calculation. Do NOT include any other text, reasoning, markdown or explanation.

{jd_info}

Candidate Resume:
{resume_text if resume_text else "No resume content provided. Assume generic match."}

Score:
""".strip()

    system_prompt = "You are an expert technical recruiter. You rate resume match strength precisely."

    try:
        raw_score = await call_llm(
            prompt=prompt,
            system_prompt=system_prompt,
            model="gpt-4.1-mini",
            throw_on_error=True,
            temperature=0.0
        )
        score_str = "".join([char for char in raw_score if char.isdigit()])
        if not score_str:
            raise ValueError("LLM returned non-numeric response: " + raw_score)
        score = int(score_str)
        score = max(0, min(100, score))
        return {"answer": str(score)}
    except Exception as e:
        logger.error(f"Error scoring resume via API: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to calculate match score using API key: {str(e)}"
        )

class WarmupRequest(BaseModel):
    resume_id: Optional[str] = None
    resume_content: Optional[str] = None
    doc_id: Optional[str] = None
    doc_content: Optional[str] = None
    prompt_id: Optional[str] = None
    prompt_content: Optional[str] = None

@router.post("/answers/warmup")
async def warmup_cache(payload: WarmupRequest):
    from app.cache.redis import redis_cache
    if payload.resume_id and payload.resume_content:
        await redis_cache.set_resume(payload.resume_id, payload.resume_content)
    if payload.doc_id and payload.doc_content:
        await redis_cache.set_cached_item(f"knowledge:{payload.doc_id}", json.dumps({
            "name": "Reference Document",
            "type": "document",
            "content": payload.doc_content
        }))
    if payload.prompt_id and payload.prompt_content:
        await redis_cache.set_cached_item(f"knowledge:{payload.prompt_id}", json.dumps({
            "name": "AI Instruction Prompt",
            "type": "prompt",
            "content": payload.prompt_content
        }))
    return {"status": "success"}


class PrewarmQuestionRequest(BaseModel):
    session_id: Optional[str] = None
    partial_question: str
    resume_content: Optional[str] = None
    knowledge_content: Optional[str] = None

@router.post("/answers/prewarm-question")
async def prewarm_question_context(
    payload: PrewarmQuestionRequest
):
    """
    Pre-warms the full prompt context into Redis as soon as the interviewer starts
    forming a question. By the time the user clicks Answer, context is already cached
    and the backend skips straight to the LLM call — eliminating preparation latency.
    """
    import asyncio
    from app.services.prompt_builder import prompt_builder
    from app.services.transcript_intelligence import transcript_engine
    from app.services.context_orchestrator import context_orchestrator
    from app.db.database import SessionLocal

    try:
        session_id_parsed = uuid.UUID(payload.session_id) if payload.session_id else None

        # Detect intent + technologies from partial question text (Synchronous call)
        analysis = transcript_engine.analyze(payload.partial_question)
        keywords = list(analysis.get("detected_technologies", set())) + list(analysis.get("keywords", []))

        # Build and cache the full prepared prompt in the background (fire-and-forget)
        async def _build_and_cache():
            try:
                # Open a safe, independent DB session specifically for the background thread
                async with SessionLocal() as db:
                    user_id = None
                    session_jd_id = None
                    db_session = None
                    resume_content = payload.resume_content
                    knowledge_content = payload.knowledge_content

                    if session_id_parsed:
                        try:
                            from app.db.models import Session
                            db_session = await db.get(Session, session_id_parsed)
                            if db_session:
                                user_id = db_session.user_id
                                session_jd_id = db_session.job_description_id
                        except Exception as dbe:
                            logger.debug(f"Pre-warm session lookup fallback: {dbe}")
                            db_session = None

                    context = await context_orchestrator.prepare_context(
                        session_id=session_id_parsed,
                        user_id=user_id,
                        db=db,
                        keywords=keywords,
                        technologies=list(analysis.get("detected_technologies", set())),
                        resume_content=resume_content,
                        knowledge_content=knowledge_content,
                        session_jd_id=session_jd_id
                    )

                    # Update prompt builder with context
                    await prompt_builder.update_session_prompt(
                        str(session_id_parsed) if session_id_parsed else "temp-session",
                        analysis,
                        context,
                        payload.partial_question
                    )

                    # Update cached session state with metadata and prewarmed_context packet
                    cached_session = await redis_cache.get_session_state(payload.session_id)
                    if not cached_session:
                        cached_session = {}
                    
                    role_info = f"Role: {db_session.role_name if db_session else 'Software Engineer'} at {db_session.company_name if db_session else 'Target Company'}"
                    cached_session.update({
                        "metadata_loaded": True,
                        "resume_context": context["resume_context"],
                        "knowledge_context": context["knowledge_context"],
                        "jd_context": context.get("jd_context", "None loaded."),
                        "previous_context": context["previous_context"],
                        "reasoning_focus": context["reasoning_focus"],
                        "prewarmed_context": {
                            "role_info": role_info,
                            "jd_context": context.get("jd_context", "None loaded."),
                            "resume_context": context.get("resume_context", "None loaded."),
                            "candidate_memories": context.get("knowledge_context", "None loaded."),
                            "previous_context": context.get("previous_context", "None."),
                            "keywords": keywords
                        }
                    })
                    await redis_cache.set_session_state(payload.session_id, cached_session)

                logger.info(f"Pre-warm complete for session {payload.session_id}: '{payload.partial_question[:60]}...'")
            except Exception as e:
                logger.warning(f"Pre-warm background task failed: {e}")

        asyncio.ensure_future(_build_and_cache())
        return {"status": "pre-warming"}
    except Exception as e:
        logger.warning(f"Pre-warm request error: {e}")
        return {"status": "skipped"}

class SessionOverviewRequest(BaseModel):
    session_id: Optional[str] = None
    transcript: str
    qa_history: Optional[str] = None

@router.post("/sessions/overview")
async def generate_session_overview(payload: SessionOverviewRequest):
    system_prompt = (
        "You are an expert interviewer. You review the candidate's live interview transcripts "
        "and Q&A answers, and provide a concise overview of their performance: key strengths, "
        "improvement points, and recommendations. Respond in clear, professional paragraphs."
    )
    prompt = (
        f"Based on the following transcript and Q&A history, generate a structured overview of the session:\n\n"
        f"=== TRANSCRIPT ===\n{payload.transcript}\n\n"
    )
    if payload.qa_history:
        prompt += f"=== Q&A HISTORY ===\n{payload.qa_history}\n\n"
        
    try:
        raw_overview = await call_llm(
            prompt=prompt,
            system_prompt=system_prompt,
            model="gpt-4.1-mini",
            throw_on_error=True,
            temperature=0.3
        )
        return {"overview": raw_overview}
    except Exception as e:
        logger.error(f"Error generating session overview: {e}")
        return {"overview": "Failed to generate AI session overview."}
