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
from app.services.context_service import build_session_context
from app.services.ai_service import call_gemini, call_llm, stream_llm
from app.cache.redis import redis_cache

router = APIRouter()
logger = logging.getLogger("copilotx.answers")

def _log_prompt_to_file(question: str, system_prompt: str, user_prompt: str, prompt_type: str, source_type: str):
    """Write the exact system + user prompt sent to the LLM into logs/prompt_debug/ for debugging."""
    try:
        # Save logs absolutely under the backend/logs/prompt_debug directory
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

def get_system_prompt() -> str:
    from datetime import datetime
    now = datetime.utcnow()
    current_date_str = now.strftime("%B %d, %Y")

    return f"""
You are a real person sitting in a technical interview today, {current_date_str}.
You are NOT an AI. You are NOT a chatbot. You are the candidate.

Your only job is to answer the interviewer's question the way a real, experienced engineer would naturally say it out loud.

HOW TO SPEAK:
- Give a detailed, structured answer using bullet points. Each major point MUST have a bold side heading followed by explanation.
- Format each bullet as: **Heading:** explanation text. Example: **Horizontal Scaling:** I'd add more instances behind a load balancer rather than scaling up a single machine.
- Use bold headings (**like this:**) to highlight each distinct point or concept. This is mandatory for every bullet.
- Talk the way a confident engineer talks — direct, clear, with contractions: I've, I'd, I'm, that's, it's.
- Start directly with the first bullet. Do NOT warm up with filler or intro sentences.
- Each bullet should be a complete, self-contained point.

SOUND LIKE A REAL PERSON:
- Speak naturally within each bullet: "Yeah so...", "In my experience...", "Basically...", "One thing I've found is..."
- Do NOT say: "Certainly", "Great question", "Absolutely", "Of course", "Sure", "Here's the answer", "As an AI".
- Do NOT repeat the question or start with a header.
- Do NOT add a conclusion or summary at the end.

TECHNICAL QUESTIONS:
- Explain concepts conversationally within each bullet point.
- Never output code blocks, raw syntax, or complexity numbers. Describe the logic in plain English.

FACTS:
- Only mention things that are in your resume and background context.
- If you don't know something, say so naturally: "Honestly I haven't worked with that directly".

OUTPUT FORMAT:
Return ONLY valid JSON with exactly two keys.
{{"question": "<the interviewer's question, cleaned up>", "answer": "<your answer as bullet points — EACH bullet MUST start with a bold heading like **Heading:** followed by explanation. Use \\n- to separate bullets. No asterisks except for bold headings. No numbered lists."}}
"""	.strip()


def get_hr_system_prompt() -> str:
    from datetime import datetime
    now = datetime.utcnow()
    current_date_str = now.strftime("%B %d, %Y")

    return f"""
You are a real candidate in an HR interview today, {current_date_str}.
You are NOT an AI. You are a person. Speak like one.

The HR interviewer is asking you questions about yourself, your experience, your personality, and why you want this role.
Answer like a real human being sitting across the table.

HOW TO SPEAK:
- Give a structured answer using bullet points. Each major point MUST have a bold side heading.
- Format each bullet as: **Heading:** explanation. Example: **Why this role:** I've been working in distributed systems for a while and this team's scale genuinely excites me.
- Use bold headings (**like this:**) for every distinct point — this is mandatory.
- Talk naturally within each bullet — contractions, casual phrasing, first person. I'm, I've, I'd, that's.
- Get to the first bullet immediately. No warm-up, no preamble.

SEEMINGLY SMALL THINGS THAT MATTER:
Never say: "Certainly", "Great question", "Absolutely", "Of course", "Sure", "I'd be happy to", "As an AI".
Never say "Based on the resume" or "According to the resume" — you ARE the person, speak from experience.
Never use placeholder text like "[Company Name]" or "[Your Name]".
If you don't know the company name from context, speak generally about the role and domain instead.

TONE:
Confident but not arrogant. Honest. Grounded.
When talking about strengths: specific, not generic.
When talking about weaknesses: be real — something you're genuinely working on.
When talking about motivation: make it personal.

FACTS:
Only use what's in the resume and context provided. Never invent companies, roles, or achievements.
If something isn't in context, handle it naturally: "That's not something I've encountered yet, but how I'd approach it is..."

OUTPUT FORMAT:
Return ONLY valid JSON with exactly two keys.
{{"question": "<the interviewer's question, cleaned up>", "answer": "<your answer as bullet points — EACH bullet MUST start with **Heading:** followed by explanation. Use \\n- to separate bullets. No numbered lists."}}
""".strip()


def get_coding_system_prompt() -> str:
    from datetime import datetime
    now = datetime.utcnow()
    current_date_str = now.strftime("%B %d, %Y")
    
    return f"""
You are the interview candidate attending a technical coding round.

Forget that you are an AI assistant or ChatGPT.

The current date is {current_date_str}.

------------------------------------------------------------
CORE PRINCIPLES (CODING ROUND)
------------------------------------------------------------
• Provide highly optimized, clean, and bug-free code solutions.
• Keep your explanations concise, professional, and direct. Explain like you are talking to another senior engineer.
• For any coding question, you MUST return the fully implemented code solution inside the response. The code block (wrapped in appropriate markdown triple-backticks) MUST include clear, detailed comments explaining every single line of code. You MUST also provide a clear explanation of the approach along with the Time Complexity and Space Complexity.
• Avoid generic, scripted, or AI-sounding preambles (do NOT say "Sure!", "Certainly", "Here is the code", etc.).

------------------------------------------------------------
OUTPUT FORMAT
------------------------------------------------------------
You MUST return ONLY valid JSON. The "answer" field MUST contain the code block (wrapped in triple backticks with the language name), the line-by-line comments, the approach explanation, and complexities.

Example JSON output structure (ensure all newlines inside string values are escaped as \\n, and double quotes are escaped as \\"):
{{
  "question": "<cleaned interviewer question>",
  "answer": "< Excutable Code>"
}}
""".strip()

def get_screenshot_coding_system_prompt() -> str:
    return """
You are a world-class senior multi-disciplinary technical expert, engineering architect, and master vision solver.
Analyze the provided screenshot with 100% precision and provide an exhaustive, 360-degree deep solution and complete technical breakdown across ANY domain.

SUPPORTED DOMAIN COVERAGE (AUTO-DETECT & SOLVE):
• Electronics, VLSI & Microcontrollers: IC Pinouts (8051, 555, ARM, Arduino, ESP32), Logic Circuits, Verilog/VHDL, MOSFETs, PCBs, Timing Diagrams.
• Networking, Cloud & Infrastructure: Cisco Meraki, IP Subnetting, VLANs, BGP/OSPF, OSI Layers, AWS/Azure/GCP Cloud Architecture, Wireshark.
• Mechanical, Civil & CAD/CAM: 2D/3D CAD Models (SolidWorks, AutoCAD), Orthographic Blueprints, Stress/FEA Analysis, Thermodynamics.
• Computer Science & AI/ML: LeetCode/HackerRank Algorithms, IDE Stack Traces, SQL, ER/UML Diagrams, Neural Networks, Flowcharts.
• Physics, Chemistry & Biomedical: Kinematics, Circuit Analysis, Chemical Reaction Pathways, Optics, Control Systems.
• Mathematics, Aptitude & MCQs: Calculus, Geometry, Chart/Graph Data Interpretation, Technical Assessment MCQs.

UNIVERSAL 360-DEGREE EXHAUSTIVE ANALYSIS MANDATES:

1. FULL FORMS & TERMINOLOGY BREAKDOWN:
   - Identify and expand EVERY acronym, abbreviation, protocol, component tag, or technical term in the screenshot (e.g., VLSI, UART, BGP, GPIO, CAD, IC numbers, OSI layers, MOSFET, etc.).
   - Explain the specific role of each term/protocol in the context of the diagram.

2. PIN DIAGRAM, PORT & COMPONENT SPECIFICATIONS:
   - If ICs, microcontrollers, logic chips, switches, or hardware ports are present, detail the pinout/pin diagram, signal directions, power supply (VCC/GND), and pin functions.

3. WORKING PRINCIPLE & OPERATIONAL MECHANISM:
   - Explain in detail HOW the system, circuit, network, model, or algorithm works from first principles.
   - Describe the underlying physical, electrical, logical, or mechanical mechanisms clearly.

4. FLOW OF DIAGRAM & STEP-BY-STEP SIGNAL/DATA SEQUENCE:
   - Detail the exact step-by-step flow across the diagram: input signal/trigger → processing nodes → output states.
   - Trace packet travel paths, current/voltage flows, structural load propagation, or data pipeline execution.

5. PREDICTED NEXT STEPS, WORKFLOW EXECUTION & TROUBLESHOOTING:
   - Predict the immediate next steps in the operational sequence or execution flow.
   - Provide potential failure points, diagnostic checks, or recommended next actions for troubleshooting.

6. DIRECT SOLUTION, CALCULATIONS & OPTIMAL CODE:
   - Answer any specific question, calculation, or exercise with exact values and step-by-step math.
   - For coding/algorithms, detect the EXACT programming language shown or implied (Java, C++, C, Python, Verilog/VHDL, JS, SQL, Rust, Go) and write the complete, optimal, bug-free code solution.
   - Highlight correct options for MCQs.

JSON OUTPUT FORMAT:
Return ONLY valid JSON.
{
  "question": "<1-sentence clean summary of the problem in the screenshot>",
  "answer": "<complete, exhaustive 360-degree technical solution covering Full Forms, Working Principle, Pin Diagram/Specs, Flow Sequence, Predicted Next Steps, and Exact Answer/Code>"
}
""".strip()

def extract_question_from_transcript(transcript: str) -> str:
    """Extract the most recent meaningful question from a raw transcript."""
    import re
    if not transcript or len(transcript.strip()) < 5:
        return transcript
    
    clean_t = transcript.strip()
    # If the new segment since the last answer is relatively short, return the whole thing
    if len(clean_t) < 250:
        return clean_t

    # Split on sentence-ending punctuation
    sentences = re.split(r'(?<=[.?!])\s+', clean_t)
    sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 8]
    if not sentences:
        return clean_t
    # Score each sentence by question-likelihood
    question_words = [
        'what', 'how', 'why', 'when', 'where', 'which', 'who',
        'can you', 'could you', 'tell me', 'explain', 'describe',
        'walk me', 'have you', 'do you', 'did you', 'are you',
        'polish', 'improve', 'optimize', 'rewrite', 'please',
        'design', 'implement', 'build', 'write', 'create'
    ]
    best_sentence = None
    best_score = -1
    for s in reversed(sentences):
        s_lower = s.lower()
        score = 0
        if s.endswith('?'):
            score += 5
        for qw in question_words:
            if qw in s_lower:
                score += 2
                break
        # Prefer longer sentences (more likely to be an actual question)
        score += min(len(s.split()), 20) * 0.1
        if score > best_score:
            best_score = score
            best_sentence = s
    # Fall back to last 2 sentences if nothing stood out
    if best_score < 1:
        best_sentence = ' '.join(sentences[-2:]) if len(sentences) >= 2 else sentences[-1]
    return best_sentence


def get_system_design_prompt() -> str:
    from datetime import datetime
    now = datetime.utcnow()
    current_date_str = now.strftime("%B %d, %Y")

    return f"""
You are a senior principal systems architect sitting in a technical system design interview today, {current_date_str}.
You are answering a system design, software architecture, microservices, database schema, or infrastructure question.

MANDATORY VISUAL ARCHITECTURE DIAGRAM (CODE BLOCK FORMAT):
1. VISUAL ARCHITECTURE DIAGRAM (CODE BLOCK):
   - At the VERY TOP of your answer, you MUST output a complete, clean, visual ASCII/Unicode Text-Art Architecture Diagram wrapped inside a code block (```text ... ```).
   - Draw clear component boxes, arrows (--->, |), client applications, API gateways, load balancers, microservices, caches (Redis), databases (PostgreSQL/Mongo), message queues (Kafka), and CDNs inside the text block.
   - Example format:
   ```text
   +-------------------------------------------------------------------------------+
   |                         SYSTEM ARCHITECTURE DIAGRAM                           |
   +-------------------------------------------------------------------------------+
   |  [ Client App ] ---> [ API Gateway / Nginx ] ---> [ Auth Service ]            |
   |                             |                                                 |
   |                             v                                                 |
   |                    [ Microservice Cluster ]                                   |
   |                             |                                                 |
   |                   +---------+---------+                                       |
   |                   |                   |                                       |
   |                   v                   v                                       |
   |           [ Redis Cache ]     [ Kafka Queue ]                                 |
   |                   |                   |                                       |
   |                   v                   v                                       |
   |          [ PostgreSQL DB ]   [ ElasticSearch ]                                |
   +-------------------------------------------------------------------------------+
   ```

2. DETAILED TECHNICAL BREAKDOWN (BELOW DIAGRAM):
   - Immediately below the visual architecture diagram code block, provide a comprehensive, step-by-step breakdown using bold headings (**Component:** explanation).
   - Detail the data flow, storage layer, caching strategy, load balancing, async queues, scalability, fault tolerance, and trade-offs.

OUTPUT FORMAT:
Return ONLY valid JSON with exactly two keys.
{{"question": "<the interviewer's question, cleaned up>", "answer": "<architecture diagram code block followed by detailed bulleted breakdown with bold headings>"}}
""".strip()


def resolve_system_prompt_type(latest_question: str, session_category: str = "", session_name: str = "") -> tuple[str, str]:
    q_lower = latest_question.lower()
    session_category_lower = session_category.lower()
    session_name_lower = session_name.lower() if session_name else ""
    
    # 1. HR/Behavioral check
    hr_triggers = [
        "salary", "strength", "weakness", "career goal", "why this company", "compensation", "benefits",
        "why should we hire", "conflict", "disagreement", "challenge", "teamwork", "leadership", "behavioral",
        "tell me about yourself", "introduce yourself", "walk me through your resume", "walk me through your background"
    ]
    is_hr = (session_category_lower in ["hr", "behavioral"]) or any(t in q_lower for t in hr_triggers)
    if is_hr:
        return get_hr_system_prompt(), "hr"
        
    # 2. System Design / Architecture check
    design_triggers = [
        "design", "architecture", "microservice", "infrastructure", "topology", "component diagram",
        "database schema", "er diagram", "flowchart", "how would you build", "how would you scale",
        "rate limiter", "load balancer", "kafka", "redis", "sharding", "system design", "distributed system"
    ]
    is_design = (session_category_lower in ["system design", "architecture"]) or any(t in q_lower for t in design_triggers)
    if is_design:
        return get_system_design_prompt(), "system_design"
    
    # 3. Default: existing system prompt (Interview category)
    return get_system_prompt(), "interview"


@router.post("/answer", response_model=AnswerResponse)
async def generate_answer(
    payload: AnswerRequest,
    db: AsyncSession = Depends(get_db)
):
    session = None
    session_uuid = None
    if payload.session_id:
        try:
            session_uuid = uuid.UUID(payload.session_id) if isinstance(payload.session_id, str) and len(payload.session_id) == 36 else (payload.session_id if isinstance(payload.session_id, uuid.UUID) else None)
            if session_uuid:
                session_repo = SessionRepository(db)
                session = await session_repo.get_by_id(session_uuid)
        except Exception:
            pass

    raw_transcript = payload.transcript or ""
    # For transcript mode, extract just the actual question from the full noisy transcript
    if payload.question:
        latest_question = payload.question
    elif raw_transcript:
        latest_question = extract_question_from_transcript(raw_transcript)
    else:
        latest_question = ""


    # Detect if this is an HR, Coding, or Interview session category or question
    session_category = ""
    session_name = ""
    if payload.session_id:
        try:
            cached_session = await redis_cache.get_session_state(str(payload.session_id))
            if cached_session:
                session_category = cached_session.get("category", "")
        except Exception:
            pass
        if session:
            session_name = session.session_name

    if payload.source_type == "screenshot":
        sys_prompt = get_screenshot_coding_system_prompt()
        prompt_type = "coding"
    else:
        sys_prompt, prompt_type = resolve_system_prompt_type(
            latest_question,
            session_category,
            session_name
        )
    context_prompt = None
    
    if payload.session_id and payload.source_type != "transcript":
        try:
            cached_session = await redis_cache.get_session_state(str(payload.session_id))
            if cached_session and "prepared_prompt" in cached_session:
                prompt_data = json.loads(cached_session["prepared_prompt"])
                user_p = prompt_data.get("user_prompt", "")
                # Only use cached prompt if the current question is actually inside it
                question_in_cache = latest_question and len(latest_question) > 5 and latest_question.lower()[:40] in user_p.lower()
                if user_p and question_in_cache:
                    context_prompt_data = prompt_data.get("system_prompt", "")
                    base_prompt, _ = resolve_system_prompt_type(latest_question, session_category, session_name)
                    sys_prompt = f"{base_prompt}\n\n{context_prompt_data}"
                    context_prompt = user_p
                    logger.info(f"Loaded matching prepared prompt from cache for session {payload.session_id}")
                else:
                    logger.info(f"Prepared prompt is stale/mismatched — rebuilding from DB for: {latest_question[:60]}")
        except Exception as e:
            logger.warning(f"Error loading prepared prompt from Redis: {e}")

    if not context_prompt:
        context_prompt = await build_session_context(
            session_id=session_uuid,
            latest_question=latest_question,
            db=db,
            resume_content=payload.resume_content,
            knowledge_content=payload.knowledge_content
        )

    # CHECK FOR STORED INTRODUCTION
    stored_introduction = None
    resume_obj = None
    if payload.resume_content and len(payload.resume_content) < 100:
        try:
            res_uuid = uuid.UUID(payload.resume_content)
            from app.db.models import Resume
            resume_obj = await db.get(Resume, res_uuid)
        except Exception:
            pass
    if not resume_obj and session and session.user_id:
        try:
            from app.db.repositories import ResumeRepository
            resume_repo = ResumeRepository(db)
            resume_obj = await resume_repo.get_active(session.user_id)
        except Exception:
            pass

    # Self-healing logic for old resumes that don't have summaries generated yet
    if resume_obj and not resume_obj.introduction:
        try:
            logger.info(f"[Self-Healing] Generating missing summaries for resume: {resume_obj.file_name}")
            from app.services.ai_service import generate_resume_summaries
            summaries = await generate_resume_summaries(resume_obj.parsed_content)
            resume_obj.introduction = summaries.get("introduction")
            resume_obj.professional_summary = summaries.get("professional_summary")
            resume_obj.career_journey = summaries.get("career_journey")
            resume_obj.strengths = summaries.get("strengths")
            resume_obj.project_summary = summaries.get("project_summary")
            db.add(resume_obj)
            await db.commit()
            logger.info("[Self-Healing] Summaries successfully generated and saved to DB.")
        except Exception as she:
            logger.warning(f"[Self-Healing] Failed to generate resume summaries: {she}")

    if resume_obj and resume_obj.introduction:
        q_clean = latest_question.lower().strip().replace("?", "").replace(".", "").replace(",", "")
        triggers = [
            "tell me about yourself",
            "introduce yourself",
            "walk me through your resume",
            "walk me through your background",
            "explain your experience",
            "tell me about your experience",
            "talk about yourself",
            "who are you",
            "intro",
            "introduction"
        ]
        if any(t in q_clean for t in triggers):
            stored_introduction = resume_obj.introduction
            logger.info("Found stored introduction for question: " + latest_question)

    if stored_introduction:
        base_prompt, _ = resolve_system_prompt_type(latest_question, session_category, session_name)
        sys_prompt = (
            f"{base_prompt}\n\n"
            "SPECIAL TASK: You are a spoken introduction polisher.\n"
            "You will receive a pre-written candidate introduction below.\n"
            "Your ONLY job is to polish the grammar, readability, and natural spoken flow so it sounds perfect for a 3-minute verbal interview delivery.\n"
            "Rules:\n"
            "- Keep ALL original facts, dates, technologies, company names, and achievements exactly as they are.\n"
            "- Do NOT add, invent, or remove any facts.\n"
            "- Do NOT adapt content to match the job role or company from context.\n"
            "- Do NOT say 'Sure!', 'Certainly', 'Of course', 'Absolutely', or any AI preamble.\n"
            "- Start directly with 'I am...' or similar — no greeting.\n"
            "- Return ONLY valid JSON: {\"question\": \"<cleaned question>\", \"answer\": \"<polished introduction>\"}"
        )
        context_prompt = f"Pre-written Introduction:\n{stored_introduction}"

    # 2. Call LLM
    # --- DEBUG: log what we actually send to the model ---
    _source = payload.source_type if payload.source_type in ("manual", "transcript", "screenshot") else ("manual" if payload.question else "transcript")
    _log_prompt_to_file(
        question=latest_question,
        system_prompt=sys_prompt,
        user_prompt=context_prompt or "",
        prompt_type=prompt_type,
        source_type=_source
    )
    raw_response = await call_gemini(
        prompt=context_prompt,
        system_prompt=sys_prompt,
        response_json=True,
        model=payload.model
    )

    # 3. Parse Gemini Response
    question = latest_question
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
        question = data.get("question", latest_question).strip() or latest_question
        answer = data.get("answer", raw_response).strip()
    except Exception as e:
        logger.warning(f"Failed to parse Gemini response as JSON: {e}. Raw response: {raw_response}")

    # 4. Save to Database (only if session exists)
    if session and session_uuid:
        qa_repo = QARepository(db)
        qa = await qa_repo.create(
            session_id=session_uuid,
            question=question,
            answer=answer,
            source_type=payload.source_type
        )
        try:
            cached_session = await redis_cache.get_session_state(str(payload.session_id))
            if cached_session:
                prev_ctx = cached_session.get("previous_context", "")
                # Truncate answer to 150 chars so stale long answers don't pollute future context
                answer_snippet = answer[:150] + "..." if len(answer) > 150 else answer
                new_entry = f"Q: {question}\nA: {answer_snippet}"
                if prev_ctx and prev_ctx != "None.":
                    parts = [p.strip() for p in prev_ctx.split("Q: ") if p.strip()]
                    parts.append(f"{question}\nA: {answer_snippet}")
                    cached_session["previous_context"] = "\n".join([f"Q: {p}" for p in parts[-2:]])
                else:
                    cached_session["previous_context"] = new_entry
                await redis_cache.set_session_state(str(payload.session_id), cached_session)
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
            "source_type": payload.source_type,
            "created_at": datetime.datetime.utcnow()
        }

@router.post("/answer/stream")
async def generate_answer_stream(
    payload: AnswerRequest,
    db: AsyncSession = Depends(get_db)
):
    session = None
    session_uuid = None
    if payload.session_id:
        try:
            session_uuid = uuid.UUID(payload.session_id) if isinstance(payload.session_id, str) and len(payload.session_id) == 36 else (payload.session_id if isinstance(payload.session_id, uuid.UUID) else None)
            if session_uuid:
                session_repo = SessionRepository(db)
                session = await session_repo.get_by_id(session_uuid)
        except Exception:
            pass

    raw_transcript = payload.transcript or ""
    # For transcript mode, extract just the actual question from the full noisy transcript
    if payload.question:
        latest_question = payload.question
    elif raw_transcript:
        latest_question = extract_question_from_transcript(raw_transcript)
    else:
        latest_question = ""


    # Detect if this is an HR, Coding, or Interview session category or question
    session_category = ""
    session_name = ""
    if payload.session_id:
        try:
            cached_session = await redis_cache.get_session_state(str(payload.session_id))
            if cached_session:
                session_category = cached_session.get("category", "")
        except Exception:
            pass
        if session:
            session_name = session.session_name

    if payload.source_type == "screenshot":
        sys_prompt = get_screenshot_coding_system_prompt()
        prompt_type = "coding"
    else:
        sys_prompt, prompt_type = resolve_system_prompt_type(
            latest_question,
            session_category,
            session_name
        )
    context_prompt = None
    
    if payload.session_id and payload.source_type != "transcript":
        try:
            cached_session = await redis_cache.get_session_state(str(payload.session_id))
            if cached_session and "prepared_prompt" in cached_session:
                prompt_data = json.loads(cached_session["prepared_prompt"])
                user_p = prompt_data.get("user_prompt", "")
                # Only use cached prompt if the current question is actually inside it
                question_in_cache = latest_question and len(latest_question) > 5 and latest_question.lower()[:40] in user_p.lower()
                if user_p and question_in_cache:
                    context_prompt_data = prompt_data.get("system_prompt", "")
                    base_prompt, _ = resolve_system_prompt_type(latest_question, session_category, session_name)
                    sys_prompt = f"{base_prompt}\n\n{context_prompt_data}"
                    if prompt_type == "coding":
                        user_p = user_p.replace(
                            "Provide your verbal guidance or response based on the candidate's context.",
                            "Provide the fully implemented optimized code block inside the response as specified in the coding round prompt."
                        )
                    context_prompt = user_p
                    logger.info(f"Loaded matching prepared prompt from cache for session {payload.session_id}")
                else:
                    logger.info(f"Prepared prompt is stale/mismatched — rebuilding from DB for: {latest_question[:60]}")
        except Exception as e:
            logger.warning(f"Error loading prepared prompt from Redis: {e}")

    if not context_prompt:
        context_prompt = await build_session_context(
            session_id=session_uuid,
            latest_question=latest_question,
            db=db,
            resume_content=payload.resume_content,
            knowledge_content=payload.knowledge_content
        )

    # CHECK FOR STORED INTRODUCTION
    stored_introduction = None
    resume_obj = None
    if payload.resume_content and len(payload.resume_content) < 100:
        try:
            res_uuid = uuid.UUID(payload.resume_content)
            from app.db.models import Resume
            resume_obj = await db.get(Resume, res_uuid)
        except Exception:
            pass
    if not resume_obj and session and session.user_id:
        try:
            from app.db.repositories import ResumeRepository
            resume_repo = ResumeRepository(db)
            resume_obj = await resume_repo.get_active(session.user_id)
        except Exception:
            pass

    # Self-healing logic for old resumes that don't have summaries generated yet
    if resume_obj and not resume_obj.introduction:
        try:
            logger.info(f"[Self-Healing] Generating missing summaries for resume in stream: {resume_obj.file_name}")
            from app.services.ai_service import generate_resume_summaries
            summaries = await generate_resume_summaries(resume_obj.parsed_content)
            resume_obj.introduction = summaries.get("introduction")
            resume_obj.professional_summary = summaries.get("professional_summary")
            resume_obj.career_journey = summaries.get("career_journey")
            resume_obj.strengths = summaries.get("strengths")
            resume_obj.project_summary = summaries.get("project_summary")
            db.add(resume_obj)
            await db.commit()
            logger.info("[Self-Healing] Summaries successfully generated and saved to DB in stream.")
        except Exception as she:
            logger.warning(f"[Self-Healing] Failed to generate resume summaries in stream: {she}")

    if resume_obj and resume_obj.introduction:
        q_clean = latest_question.lower().strip().replace("?", "").replace(".", "").replace(",", "")
        triggers = [
            "tell me about yourself",
            "introduce yourself",
            "walk me through your resume",
            "walk me through your background",
            "explain your experience",
            "tell me about your experience",
            "talk about yourself",
            "who are you",
            "intro",
            "introduction"
        ]
        if any(t in q_clean for t in triggers):
            stored_introduction = resume_obj.introduction
            logger.info("Found stored introduction for question: " + latest_question)

    if stored_introduction:
        # Stream endpoint: use plain text output (no JSON) to avoid contradictory instructions
        sys_prompt = (
            "SPECIAL TASK — Introduction Polisher.\n"
            "You will receive a pre-written candidate introduction.\n"
            "Your ONLY job: polish the grammar, readability, and natural spoken flow so it sounds perfect and highly professional for a 3-minute verbal interview delivery.\n\n"
            "STRICT RULES:\n"
            "- Keep ALL original facts, dates, technologies, company names, and achievements exactly as-is.\n"
            "- Do NOT add, invent, or remove any facts.\n"
            "- Do NOT reference the job role, company, or JD context — only use what is in the introduction.\n"
            "- Do NOT say 'Sure!', 'Certainly', 'Of course', 'Absolutely', or any AI filler.\n"
            "- Start directly with 'I am...' or 'My name is...' — no greeting, no preamble.\n"
            "- Write in a warm, confident, natural speaking voice — like a real person, not a report.\n\n"
            "OUTPUT: Return ONLY the polished spoken introduction as plain text. No JSON. No markdown. No labels."
        )
        context_prompt = f"Pre-written Introduction to polish:\n\n{stored_introduction}"

    # 2. Return StreamingResponse
    # --- DEBUG: log what we actually send to the model ---
    if payload.source_type != "screenshot" and not stored_introduction:
        import re as _re
        # Strip any remaining JSON output instruction (fragile string matching replaced by regex)
        sys_prompt = _re.sub(
            r'OUTPUT FORMAT:.*',
            'OUTPUT FORMAT:\nOutput ONLY the candidate\'s spoken response directly as plain text. Do NOT wrap it in JSON, markdown, or any other formatting. Just speak.',
            sys_prompt,
            flags=_re.DOTALL
        )

    _source_stream = payload.source_type if payload.source_type in ("manual", "transcript", "screenshot") else ("manual" if payload.question else "transcript")
    _log_prompt_to_file(
        question=latest_question,
        system_prompt=sys_prompt,
        user_prompt=context_prompt or "",
        prompt_type=prompt_type,
        source_type=_source_stream
    )
    async def stream_generator():
        accumulated_chunks = []
        async for chunk in stream_llm(
            prompt=context_prompt,
            system_prompt=sys_prompt,
            model=payload.model,
            response_json=False # Set to False for direct plain-text stream (under 1s TTFT)
        ):
            accumulated_chunks.append(chunk)
            yield chunk

        # Once stream finishes, parse and save to DB
        full_response = "".join(accumulated_chunks)
        question = latest_question
        answer = full_response

        # Check if model returned a JSON structure (fallback / backwards compatibility)
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

                # Isolate JSON object bracket block to strip any conversational prefixes/suffixes
                start_idx = cleaned.find('{')
                end_idx = cleaned.rfind('}')
                if start_idx != -1 and end_idx != -1:
                    cleaned = cleaned[start_idx:end_idx+1]

                # Use strict=False to allow literal newlines and control characters inside JSON strings
                data = json.loads(cleaned, strict=False)
                question = data.get("question", latest_question).strip() or latest_question
                answer = data.get("answer", full_response).strip()
            except Exception as e:
                logger.warning(f"Failed to parse stream response as JSON: {e}. Raw response: {full_response}")

        if session and session_uuid:
            try:
                from app.db.database import SessionLocal
                async with SessionLocal() as db_session:
                    qa_repo = QARepository(db_session)
                    await qa_repo.create(
                        session_id=session_uuid,
                        question=question,
                        answer=answer,
                        source_type=payload.source_type
                    )
                    await db_session.commit()
                    try:
                        cached_session = await redis_cache.get_session_state(str(payload.session_id))
                        if cached_session:
                            prev_ctx = cached_session.get("previous_context", "")
                            # Truncate answer to 150 chars so stale long answers don't pollute future context
                            answer_snippet = answer[:150] + "..." if len(answer) > 150 else answer
                            new_entry = f"Q: {question}\nA: {answer_snippet}"
                            if prev_ctx and prev_ctx != "None.":
                                parts = [p.strip() for p in prev_ctx.split("Q: ") if p.strip()]
                                parts.append(f"{question}\nA: {answer_snippet}")
                                cached_session["previous_context"] = "\n".join([f"Q: {p}" for p in parts[-2:]])
                            else:
                                cached_session["previous_context"] = new_entry
                            await redis_cache.set_session_state(str(payload.session_id), cached_session)
                    except Exception as e:
                        logger.warning(f"Failed to update previous_context in Redis cache inside stream generator: {e}")
            except Exception as db_err:
                logger.error(f"Failed to save QA record in streaming endpoint: {db_err}")

    return StreamingResponse(stream_generator(), media_type="text/event-stream")

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
                    resume_content = payload.resume_content
                    knowledge_content = payload.knowledge_content

                    if session_id_parsed:
                        from app.db.models import Session
                        db_session = await db.get(Session, session_id_parsed)
                        if db_session:
                            user_id = db_session.user_id
                            session_jd_id = db_session.jd_id
                            if not resume_content:
                                resume_content = db_session.resume_content
                            if not knowledge_content:
                                knowledge_content = db_session.knowledge_content

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

                    # Update cached session state with metadata so background transcript pipeline skips DB
                    cached_session = await redis_cache.get_session_state(payload.session_id)
                    if not cached_session:
                        cached_session = {}
                    cached_session.update({
                        "metadata_loaded": True,
                        "resume_context": context["resume_context"],
                        "knowledge_context": context["knowledge_context"],
                        "previous_context": context["previous_context"],
                        "reasoning_focus": context["reasoning_focus"]
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
