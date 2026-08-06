from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Dict, Optional
import json
import logging
from app.services.ai_service import call_gemini

router = APIRouter()
logger = logging.getLogger("copilotx.mock_interviews")

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

MOCK_QUESTION_BANK = {
    "Interview": [
        'Tell me about a time you handled a production issue.',
        'Explain Redis caching strategy and when you would use it.',
        'What is the difference between synchronous and asynchronous communication?',
        'Describe a situation where you disagreed with a teammate.',
        'Design a scalable URL shortener.',
        'How would you optimize a slow SQL query?',
        'How do you handle prioritization when working on multiple high-priority tasks?',
        'What is the difference between SQL and NoSQL databases, and how do you choose?'
    ],
    "Coding": [
        'Write an optimal solution for Two Sum.',
        'Find the longest substring without repeating characters.',
        'Merge overlapping intervals.',
        'Design a data structure that supports insert, delete, and getRandom in O(1) time.'
    ],
    "HR": [
        'Why do you want to join this company?',
        'What are your salary expectations?',
        'Why are you looking for a change?',
        'Where do you see yourself in 5 years?'
    ],
    "Mixed": [
        'Tell me about yourself and your recent project experience.',
        'How would you design a scalable notification system?',
        'How would you optimize a slow SQL query?',
        'Tell me about a time you solved a difficult production issue.',
        'Write an optimal solution for Two Sum.'
    ]
}

@router.post("/mock-interview/feedback")
async def mock_interview_feedback(req: MockFeedbackRequest):
    # 1. Attempt to generate feedback & score via AI LLM
    system_prompt = (
        "You are an expert technical interviewer conducting a professional mock interview.\n"
        "Your goal is to evaluate the candidate's answer, score it dynamically from 0 to 100 based on STAR structure, technical depth, and metrics, provide a model suggested answer, and generate the next interview question.\n"
        "You must respond in raw JSON format with exactly four fields:\n"
        "1. \"score\": An integer between 0 and 100 representing the performance score for this answer.\n"
        "2. \"feedback\": Concise, structured, and constructive feedback on the candidate's answer, highlighting strengths and improvements.\n"
        "3. \"suggested_answer\": A high-quality model response demonstrating how a top candidate would answer this question.\n"
        "4. \"next_question\": The next logical interview question, customized to the target company, target role, job description, and the candidate's previous responses.\n\n"
        "Do NOT return markdown formatting like ```json. Just return a raw JSON string."
    )

    history_str = "\n".join([f"Q: {h.get('question', '')}\nA: {h.get('answer', '')}" for h in req.history])
    
    prompt = (
        f"Target Company: {req.company}\n"
        f"Target Role: {req.role}\n"
        f"Interview Type: {req.interview_type}\n"
        f"Job Description: {req.jd}\n\n"
        f"Current Question: {req.question}\n"
        f"Candidate's Answer: {req.user_answer}\n\n"
        f"Conversation History (previous questions and answers):\n{history_str}\n\n"
        f"Please generate score, feedback, suggested_answer, and next_question in JSON format."
    )

    try:
        from app.core.config import settings
        mock_model = req.model
        if settings.GROQ_API_KEY:
            mock_model = settings.GROQ_MODEL
        ai_resp = await call_gemini(prompt, system_prompt, response_json=True, model=mock_model)
        if ai_resp and ai_resp.strip():
            cleaned = ai_resp.strip()
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                if lines[0].startswith("```json") or lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].startswith("```"):
                    lines = lines[:-1]
                cleaned = "\n".join(lines).strip()
            
            data = json.loads(cleaned)
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
        "I would answer this by giving a concrete project example, explaining the technical decision I made, showing ownership, and ending with measurable impact."
    )

    questions = MOCK_QUESTION_BANK.get(req.interview_type, MOCK_QUESTION_BANK["Mixed"])
    asked = {h.get("question", "").lower() for h in req.history}
    asked.add(req.question.lower())
    
    next_q = None
    for q in questions:
        if q.lower() not in asked:
            next_q = q
            break
    if not next_q:
        next_q = "Thank you! That concludes our mock interview. Do you have any questions for me?"

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
        from app.core.config import settings
        mock_model = req.model
        if settings.GROQ_API_KEY:
            mock_model = settings.GROQ_MODEL
        ai_resp = await call_gemini(prompt, system_prompt, response_json=True, model=mock_model)
        if ai_resp and ai_resp.strip():
            cleaned = ai_resp.strip()
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                if lines[0].startswith("```json") or lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].startswith("```"):
                    lines = lines[:-1]
                cleaned = "\n".join(lines).strip()
            
            data = json.loads(cleaned)
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
