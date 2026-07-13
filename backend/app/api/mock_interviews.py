from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Dict
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

MOCK_QUESTION_BANK = {
    "Behavioral": [
        'Tell me about a time you handled a production issue.',
        'Describe a situation where you disagreed with a teammate.',
        'Tell me about a time you had to learn a new technology quickly.',
        'How do you handle prioritization when working on multiple high-priority tasks?'
    ],
    "Technical": [
        'Explain Redis caching strategy and when you would use it.',
        'What is the difference between synchronous and asynchronous communication?',
        'How would you reduce latency in a realtime WebSocket application?',
        'What is the difference between SQL and NoSQL databases, and how do you choose?'
    ],
    "Coding": [
        'Write an optimal solution for Two Sum.',
        'Find the longest substring without repeating characters.',
        'Merge overlapping intervals.',
        'Design a data structure that supports insert, delete, and getRandom in O(1) time.'
    ],
    "SQL": [
        'Write a SQL query to find duplicate records in a table.',
        'How would you optimize a slow SQL query?',
        'Explain the difference between RANK, DENSE_RANK, and ROW_NUMBER.',
        'How would you design a schema and query to track user logins and find active users?'
    ],
    "System Design": [
        'Design a scalable URL shortener.',
        'Design a notification system.',
        'Design a rate limiter for an API.',
        'Design a distributed message queue like Kafka.'
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
    # 1. Attempt to generate via Gemini first if configured
    system_prompt = (
        "You are an expert technical interviewer conducting a professional mock interview.\n"
        "Your goal is to evaluate the candidate's answer, provide a model suggested answer, and generate the next interview question.\n"
        "You must respond in JSON format with exactly three fields:\n"
        "1. \"feedback\": Concise, structured, and constructive feedback on the candidate's answer, highlighting strengths and improvements.\n"
        "2. \"suggested_answer\": A high-quality model response demonstrating how a top candidate would answer this question.\n"
        "3. \"next_question\": The next logical interview question, customized to the target company, target role, job description, and the candidate's previous responses. Keep the question professional and aligned with the interview type.\n\n"
        "Do NOT return markdown formatting like ```json or anything else. Just return a raw JSON string."
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
        f"Please generate the feedback, suggested_answer, and next_question in JSON format."
    )

    try:
        from app.core.config import settings
        mock_model = req.model
        if settings.GROQ_API_KEY:
            mock_model = settings.GROQ_MODEL
        ai_resp = await call_gemini(prompt, system_prompt, response_json=True, model=mock_model)
        if ai_resp and ai_resp.strip():
                # Clean up any potential markdown wraps just in case
                cleaned = ai_resp.strip()
                if cleaned.startswith("```"):
                    lines = cleaned.splitlines()
                    if lines[0].startswith("```json") or lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines[-1].startswith("```"):
                        lines = lines[:-1]
                    cleaned = "\n".join(lines).strip()
                
                data = json.loads(cleaned)
                feedback = data.get("feedback")
                suggested = data.get("suggested_answer")
                next_q = data.get("next_question")
                if feedback and suggested and next_q:
                    return {"feedback": feedback, "suggested_answer": suggested, "next_question": next_q}
    except Exception as e:
        logger.error(f"Error calling Gemini in mock interview endpoint: {e}")

    # 2. Local fallback if Gemini fails or is not configured
    answer_words = len(req.user_answer.split())
    if answer_words < 35:
        length_note = "Your answer is short. Add more detail, structure, and measurable impact."
    else:
        length_note = "Your answer has good detail. Improve the structure and make the ending stronger."

    if req.interview_type.lower() == "behavioral" or "time" in req.question.lower():
        feedback = (
            f"{length_note}\n\n"
            "Improve it using STAR:\n"
            "- Situation: give brief context.\n"
            "- Task: explain your responsibility.\n"
            "- Action: describe what you personally did.\n"
            "- Result: add measurable business or technical impact."
        )
        suggested = (
            "Situation: In one of my recent projects, we faced a production issue that affected reliability.\n\n"
            "Task: I was responsible for identifying the root cause and restoring stable processing.\n\n"
            "Action: I reviewed logs, isolated the failing pipeline stage, validated the changed source data, fixed the transformation logic, and added monitoring and retry handling.\n\n"
            "Result: The issue was resolved quickly, downstream impact was reduced, and we prevented recurrence through better validation and alerting."
        )
    elif req.interview_type.lower() == "system design":
        feedback = (
            f"{length_note}\n\n"
            "For system design, structure your answer with requirements, high-level architecture, database, caching, scaling, and tradeoffs."
        )
        suggested = (
            "I would start by clarifying scale, latency, availability, and consistency requirements.\n\n"
            "At a high level, I would use an API gateway, stateless services, Redis for caching or rate limiting, PostgreSQL for durable metadata, and Kafka or RabbitMQ for async processing where needed.\n\n"
            "For scaling, I would add horizontal replicas, read replicas, partitioning for high-volume data, and observability for latency and error tracking.\n\n"
            "The main tradeoff is balancing consistency, latency, cost, and operational complexity."
        )
    elif req.interview_type.lower() == "coding":
        feedback = (
            f"{length_note}\n\n"
            "For coding rounds, explain the approach briefly, cover edge cases, then provide clean optimized code and complexity."
        )
        suggested = (
            "Start with the optimal data structure, write clean code without comments, and end with:\n\n"
            "Time Complexity: O(n)\n"
            "Space Complexity: O(n)"
        )
    elif req.interview_type.lower() == "sql":
        feedback = (
            f"{length_note}\n\n"
            "For SQL, mention joins, filtering, indexes, execution plan, and query optimization where relevant."
        )
        suggested = (
            "I would first check the execution plan to identify full scans, expensive joins, sorts, and missing indexes.\n\n"
            "Then I would reduce selected columns, push filters earlier, add appropriate indexes, update statistics, and rewrite joins or aggregations if needed."
        )
    else:
        feedback = (
            f"{length_note}\n\n"
            f"Connect your answer to the {req.company} {req.role} role, add project context, and end with clear impact."
        )
        suggested = (
            "I would answer this by giving a concrete project example, explaining the technical decision I made, showing ownership, and ending with measurable impact."
        )

    # Select fallback next question (one not already asked)
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

    return {"feedback": feedback, "suggested_answer": suggested, "next_question": next_q}

