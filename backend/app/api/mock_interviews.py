from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from typing import List, Dict, Optional
import json
import re
import logging
import httpx
from app.services.ai_service import call_llm
from app.core.config import settings

router = APIRouter()
logger = logging.getLogger("copilotx.mock_interviews")

class TTSRequest(BaseModel):
    text: str
    model: str = "aura-asteria-en"

@router.post("/tts/speak")
async def tts_speak(req: TTSRequest):
    """Converts interview question text to human-like speech audio using Deepgram Aura TTS."""
    if not settings.DEEPGRAM_API_KEY:
        return Response(content=b"", status_code=400)
    
    url = f"https://api.deepgram.com/v1/speak?model={req.model}"
    headers = {
        "Authorization": f"Token {settings.DEEPGRAM_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(url, json={"text": req.text}, headers=headers)
            if resp.status_code == 200:
                return Response(content=resp.content, media_type="audio/mp3")
            else:
                logger.error(f"Deepgram TTS API error: {resp.status_code} {resp.text}")
                return Response(content=b"", status_code=resp.status_code)
    except Exception as e:
        logger.error(f"Deepgram TTS request failed: {e}")
        return Response(content=b"", status_code=500)

class MockFeedbackRequest(BaseModel):
    company: str = "Amazon"
    role: str = "Senior Software Engineer"
    interview_type: str = "Mixed"
    question: str
    user_answer: str
    jd: str = ""
    history: List[Dict[str, str]] = []
    model: str = ""

class MockEvaluateRequest(BaseModel):
    company: str = "Target Company"
    role: str = "Software Engineer"
    jd: str = ""
    history: List[Dict[str, str]] = []
    model: str = ""

class MockFirstQuestionRequest(BaseModel):
    company: str = "Target Company"
    role: str = "Software Engineer"
    interview_type: str = "Mixed"
    jd: str = ""
    model: str = ""

def extract_json(text: str) -> Optional[dict]:
    """Helper to parse raw or markdown JSON safely."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines[0].startswith("```json") or lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    
    # Direct parse
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Regex search for outer curly braces
    try:
        match = re.search(r'\{[\s\S]*\}', cleaned)
        if match:
            return json.loads(match.group(0))
    except Exception:
        pass
    return None

@router.post("/mock-interview/first-question")
async def mock_interview_first_question(req: MockFirstQuestionRequest):
    """Generates a dynamic, company and role-specific opening interview question using AI."""
    system_prompt = (
        "You are an expert executive interviewer conducting a live mock interview.\n"
        "Generate a natural, engaging, and professional opening question for the candidate tailored specifically to the target company, target role, and job description.\n"
        "Return a JSON object with exactly one field:\n"
        "{\"question\": \"Your dynamic opening interview question here...\"}"
    )
    prompt = (
        f"Target Company: {req.company}\n"
        f"Target Role: {req.role}\n"
        f"Interview Type: {req.interview_type}\n"
        f"Job Description: {req.jd}\n\n"
        "Please generate a compelling opening question tailored for this specific role."
    )
    try:
        ai_resp = await call_llm(prompt, system_prompt, model=req.model, response_json=True)
        data = extract_json(ai_resp)
        if data and data.get("question"):
            return {"question": data["question"]}
    except Exception as e:
        logger.error(f"Error generating first mock interview question: {e}")

    # Fallback opening question
    return {
        "question": f"Hello! Welcome to your mock interview at {req.company} for the {req.role} role. To get started, please introduce yourself and walk me through your background and recent project experience."
    }

@router.post("/mock-interview/feedback")
async def mock_interview_feedback(req: MockFeedbackRequest):
    """Evaluates candidate response with real AI, returning dynamic score, constructive feedback, suggested model answer, and follow-up question."""
    system_prompt = (
        "You are an expert technical interviewer conducting a professional mock interview.\n"
        "Your goal is to evaluate the candidate's answer, score it dynamically from 0 to 100 based on STAR structure, technical depth, clarity, and metrics, provide a model suggested answer, and generate the next interview question.\n"
        "You must respond in JSON format with exactly four fields:\n"
        "1. \"score\": An integer between 0 and 100 representing the performance score for this answer.\n"
        "2. \"feedback\": Concise, structured, and constructive feedback on the candidate's answer (mentioning specific strengths and improvements).\n"
        "3. \"suggested_answer\": A high-quality model response demonstrating how a top candidate would answer this question using bold bullet points (**Heading:** explanation).\n"
        "4. \"next_question\": The next logical interview question, customized to the target company, target role, and the candidate's previous response.\n\n"
        "Do NOT return markdown formatting like ```json. Just return a raw JSON string."
    )

    history_str = "\n\n".join([f"Q: {h.get('question', '')}\nA: {h.get('answer', '')}" for h in req.history])
    
    prompt = (
        f"Target Company: {req.company}\n"
        f"Target Role: {req.role}\n"
        f"Interview Type: {req.interview_type}\n"
        f"Job Description: {req.jd}\n\n"
        f"Current Question: {req.question}\n"
        f"Candidate's Answer: {req.user_answer}\n\n"
        f"Previous Interview Conversation History:\n{history_str}\n\n"
        f"Please evaluate the candidate's answer and generate score, feedback, suggested_answer, and next_question in JSON format."
    )

    try:
        ai_resp = await call_llm(prompt, system_prompt, model=req.model, response_json=True)
        data = extract_json(ai_resp)
        if data:
            score = data.get("score")
            feedback = data.get("feedback")
            suggested = data.get("suggested_answer")
            next_q = data.get("next_question")
            if feedback and suggested and next_q:
                return {
                    "score": int(score) if score is not None else 80,
                    "feedback": feedback,
                    "suggested_answer": suggested,
                    "next_question": next_q
                }
    except Exception as e:
        logger.error(f"Error calling AI in mock interview feedback endpoint: {e}")

    # Fallback heuristic feedback & score computation
    answer_words = len(req.user_answer.split())
    base_score = 60
    if answer_words >= 20: base_score += 10
    if answer_words >= 45: base_score += 15
    if any(k in req.user_answer.lower() for k in ['result', 'impact', 'built', 'designed', 'improved', 'metric']):
        base_score += 10
    calc_score = min(98, max(45, base_score))

    if answer_words < 35:
        length_note = "Your answer is short. Add more detail, structure, and measurable impact."
    else:
        length_note = "Your answer has good detail. Improve the structure and make the ending stronger."

    feedback = (
        f"{length_note}\n\n"
        f"Connect your answer directly to {req.company} {req.role} expectations, use STAR format (Situation, Task, Action, Result), and highlight measurable impact."
    )
    suggested = (
        f"**Situation & Role:** At my previous company, I led the core feature development for {req.role}.\n\n"
        "**Action & Technical Execution:** I designed the system architecture, optimized queries, and implemented caching to reduce latency.\n\n"
        "**Result & Impact:** Successfully increased system throughput by 40% and improved customer satisfaction metrics."
    )
    next_q = f"Could you elaborate on how you handled scalability and edge cases in that project?"

    return {"score": calc_score, "feedback": feedback, "suggested_answer": suggested, "next_question": next_q}


@router.post("/mock-interview/evaluate")
async def mock_interview_evaluate(req: MockEvaluateRequest):
    """Calculates dynamic overall AI score and evaluation for a completed mock interview session."""
    if not req.history:
        return {"score": 75, "summary": "No questions recorded for evaluation."}

    system_prompt = (
        "You are an executive technical interviewer evaluating a full mock interview session.\n"
        "Analyze the candidate's answers across all questions for technical depth, clarity, STAR structure, metrics, and alignment with the target role.\n"
        "Return a JSON response with exactly two fields:\n"
        "1. \"score\": An overall performance score from 0 to 100.\n"
        "2. \"summary\": A brief 2-sentence summary of overall interview performance and main area for improvement.\n\n"
        "Do NOT return markdown like ```json. Just return raw JSON string."
    )

    history_str = "\n\n".join([f"Q{i+1}: {h.get('question','')}\nA{i+1}: {h.get('answer','')}" for i, h in enumerate(req.history)])
    
    prompt = (
        f"Target Role: {req.role}\n"
        f"Target Company: {req.company}\n"
        f"Job Description: {req.jd}\n\n"
        f"Interview Transcript:\n{history_str}\n\n"
        f"Evaluate the session and return the score (0-100) and summary in JSON format."
    )

    try:
        ai_resp = await call_llm(prompt, system_prompt, model=req.model, response_json=True)
        data = extract_json(ai_resp)
        if data:
            score = data.get("score")
            summary = data.get("summary")
            if score is not None:
                return {"score": int(score), "summary": summary or "Good candidate performance."}
    except Exception as e:
        logger.error(f"Error evaluating mock interview via AI: {e}")

    # Fallback heuristic calculation
    total_score = 0
    for item in req.history:
        ans = item.get("answer", "").strip()
        words = len(ans.split())
        q_score = 60
        if words >= 20: q_score += 15
        if words >= 45: q_score += 15
        if any(w in ans.lower() for w in ['result', 'impact', 'built', 'designed', 'improved', 'scale', 'latency']):
            q_score += 10
        total_score += min(98, q_score)

    avg_score = round(total_score / len(req.history)) if req.history else 75
    return {"score": avg_score, "summary": "Calculated score based on response depth and interview structure."}
