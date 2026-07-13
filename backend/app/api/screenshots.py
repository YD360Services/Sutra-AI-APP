from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from sqlalchemy.ext.asyncio import AsyncSession
import json
import logging
import uuid
import os
from datetime import datetime

from app.db.database import get_db
from app.db.repositories import QARepository, SessionRepository
from app.schemas.answer import AnswerResponse
from app.services.screenshot_service import analyze_screenshot
from app.cache.redis import redis_cache


router = APIRouter()
logger = logging.getLogger("copilotx.screenshots")

def _log_prompt_to_file(question: str, system_prompt: str, user_prompt: str, prompt_type: str, source_type: str):
    """Write the exact system + user prompt sent to the LLM into logs/prompt_debug/."""
    try:
        # Save logs absolutely under the root logs/prompt_debug directory
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        log_dir = os.path.join(base_dir, "logs", "prompt_debug")
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        fname = os.path.join(log_dir, f"{ts}_{source_type}_{prompt_type}.txt")
        with open(fname, "w", encoding="utf-8") as f:
            f.write("=== PROMPT DEBUG LOG ===\n")
            f.write(f"Timestamp     : {datetime.utcnow().isoformat()}\n")
            f.write(f"Source Type   : {source_type}\n")
            f.write(f"Prompt Type   : {prompt_type}\n")
            f.write(f"Question      : {question}\n")
            f.write(f"\n{'='*60}\n")
            f.write(f"SYSTEM PROMPT :\n{'='*60}\n")
            f.write(system_prompt or "(empty)")
            f.write(f"\n\n{'='*60}\n")
            f.write(f"USER / CONTEXT PROMPT :\n{'='*60}\n")
            f.write(user_prompt or "(empty)")
            f.write(f"\n{'='*60}\n")
        logger.info(f"[PromptDebug] Logged prompt to {fname}")
    except Exception as e:
        logger.warning(f"[PromptDebug] Failed to write prompt log: {e}")

SCREENSHOT_SYSTEM_PROMPT = """You are a senior technical candidate sitting in a coding interview.
Analyze the provided screenshot of the screen. Find the question, coding problem, or conceptual statement visible.

ABSOLUTE RULES — NEVER BREAK THESE:
1. Your ENTIRE response must be valid JSON with exactly two keys: "question" and "answer".
2. If the screenshot contains a request to write code, build an algorithm, or solve a programming problem:
   - CRITICAL: You MUST write the code solution in the EXACT programming language shown or implied in the screenshot's code editor, starter code, or description (e.g., if you see Java syntax, classes, or imports, you MUST write the solution in Java. If you see C++, write it in C++. If you see JS, write it in JS). Do NOT default to Python unless the screenshot explicitly requests Python.
   - The "answer" value must contain the optimized code wrapped in a markdown code block using that detected language (e.g. ```cpp ... ``` or ```java ... ```), followed by a brief explanation of the complexity.
3. If the screenshot contains a conceptual, theoretical, or text-based question (such as explaining OOP concepts, definitions, system design, or HR):
   - The "answer" value must contain ONLY plain conversational text explaining the concept.
   - Do NOT include any code blocks, source code implementations, or complex syntax. Explain conversationally in simple spoken English paragraphs as a person would out loud.
4. Keep the response concise, matching the formatting specified above.
"""

from typing import Optional

@router.post("/screenshot", response_model=AnswerResponse)
async def upload_screenshot(
    file: UploadFile = File(...),
    session_id: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db)
):
    session = None
    session_uuid = None
    if session_id:
        try:
            session_uuid = uuid.UUID(session_id) if isinstance(session_id, str) and len(session_id) == 36 else (session_id if isinstance(session_id, uuid.UUID) else None)
            if session_uuid:
                session_repo = SessionRepository(db)
                session = await session_repo.get_by_id(session_uuid)
        except Exception:
            pass

    # 1. Read file into memory bytes
    try:
        image_bytes = await file.read()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to read file: {e}"
        )
    finally:
        await file.close()

    # 2. Call Vision service — always code-only prompt
    logger.info("=== SCREENSHOT: using CODE-ONLY prompt ===")
    # --- DEBUG: log what we send to the model ---
    _log_prompt_to_file(
        question="(image — see screenshot)",
        system_prompt=SCREENSHOT_SYSTEM_PROMPT,
        user_prompt="Answer the question visible in this screenshot.",
        prompt_type="coding",
        source_type="screenshot"
    )
    raw_response = await analyze_screenshot(
        image_bytes=image_bytes,
        system_prompt=SCREENSHOT_SYSTEM_PROMPT,
        model=model
    )

    # 3. Parse Response
    question = "Screenshot Question"
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
        
        # Isolate JSON object bracket block to strip any conversational prefixes/suffixes
        start_idx = cleaned.find('{')
        end_idx = cleaned.rfind('}')
        if start_idx != -1 and end_idx != -1:
            cleaned = cleaned[start_idx:end_idx+1]
        
        # Use strict=False to allow literal newlines and control characters inside JSON strings
        data = json.loads(cleaned, strict=False)
        question = data.get("question", "Screenshot Question").strip() or "Screenshot Question"
        answer = data.get("answer", raw_response).strip()
    except Exception as e:
        logger.warning(f"Failed to parse vision response as JSON: {e}. Raw response: {raw_response}")

    # 4. Save to Database (only if session exists)
    if session and session_uuid:
        qa_repo = QARepository(db)
        qa = await qa_repo.create(
            session_id=session_uuid,
            question=question,
            answer=answer,
            source_type="screenshot"
        )
        try:
            cached_session = await redis_cache.get_session_state(str(session_id))
            if cached_session:
                prev_ctx = cached_session.get("previous_context", "")
                new_entry = f"Q: {question}\nA: {answer}"
                if prev_ctx and prev_ctx != "None.":
                    parts = [p.strip() for p in prev_ctx.split("Q: ") if p.strip()]
                    parts.append(f"{question}\nA: {answer}")
                    cached_session["previous_context"] = "\n".join([f"Q: {p}" for p in parts[-2:]])
                else:
                    cached_session["previous_context"] = new_entry
                await redis_cache.set_session_state(str(session_id), cached_session)
        except Exception as e:
            logger.warning(f"Failed to update previous_context in Redis cache: {e}")
        return qa
    else:
        import datetime
        return {
            "id": uuid.uuid4(),
            "session_id": None,
            "question": question,
            "answer": answer,
            "source_type": "screenshot",
            "created_at": datetime.datetime.utcnow()
        }
