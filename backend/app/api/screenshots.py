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

SCREENSHOT_SYSTEM_PROMPT = """You are a world-class senior multi-disciplinary technical expert, engineering architect, and master vision solver.
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

7. DIRECT SOLUTION, CALCULATIONS & OPTIMAL CODE:
   - Answer any specific question, calculation, or exercise with exact values and step-by-step math.
   - For mathematical equations, write clean human-readable formulas e.g., 'z = (42 - 50) / 8 = -1' and formatted arithmetic.
   - For coding/algorithms, detect the EXACT programming language shown or implied (Java, C++, C, Python, Verilog/VHDL, JS, SQL, Rust, Go) and write the complete, optimal, bug-free code solution.
   - Highlight correct options for MCQs.

JSON OUTPUT FORMAT:
Your ENTIRE response MUST be valid JSON with exactly two keys: "question" and "answer".
{
  "question": "<1-sentence clean summary of the problem in the screenshot>",
  "answer": "<complete, exhaustive 360-degree technical solution covering Full Forms, Working Principle, Pin Diagram/Specs, Flow Sequence, Predicted Next Steps, and Exact Answer/Code>"
}
""".strip()

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
