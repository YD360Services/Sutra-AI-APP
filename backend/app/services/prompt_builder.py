import json
import logging
import time
from typing import Dict, Any, Optional
from app.cache.redis import redis_cache

logger = logging.getLogger("copilotx.prompt_builder")

SYSTEM_PROMPT_TEMPLATE = """[CONTEXT BLOCK]
Resume Context:
{resume_context}

Job Description Context:
{jd_context}

Reference Document Chunks:
{knowledge_context}

Previous Conversation Context:
{previous_context}

[SYSTEM MANDATE & INSTRUCTIONS]
You are an expert candidate answering interview questions in real time.
CRITICAL INSTRUCTIONS:
1. ALWAYS SYNC AND ALIGN YOUR ANSWERS DIRECTLY TO THE JOB DESCRIPTION (JD) AND RESUME CONTEXT ABOVE.
2. If asked to introduce yourself ("tell me about yourself", "walk me through your resume", "introduce yourself", "tell me about your background"), renovate and adapt your self-introduction so that your background, experience, accomplishments, and skills directly match and highlight the key requirements, technologies, and responsibilities in the Job Description (JD).
3. Frame your real project experiences, technical skills, and strengths around the exact requirements of the JD while staying true to the candidate's background.
4. Speak in plain, natural English — no bullet points, no asterisks, no numbered lists, no markdown of any kind. No headers, no structured formatting. Just talk naturally as a confident professional.
5. Keep it concise: 1 or 2 short paragraphs max. Get to the point quickly.
"""

class PromptBuilder:
    def __init__(self):
        pass

    def build_system_prompt(self, context: Dict[str, Any]) -> str:
        return SYSTEM_PROMPT_TEMPLATE.format(
            resume_context=context.get("resume_context", "None loaded."),
            jd_context=context.get("jd_context", "None loaded."),
            knowledge_context=context.get("knowledge_context", "None loaded."),
            previous_context=context.get("previous_context", "None."),
        )

    def build_user_prompt(self, prediction: str, latest_transcript: str) -> str:
        q = prediction if prediction else latest_transcript
        return (
            f"Interviewer Question: {q}\n\n"
            "Answer this question the way a real candidate would say it out loud in an interview. "
            "IMPORTANT: Renovate and sync your self-introduction, background, and answer directly to the Job Description (JD) and resume. "
            "Speak in plain, natural English — no bullet points, no asterisks, no numbered lists, no markdown of any kind. "
            "No headers, no structured formatting. Just talk naturally. "
            "One or two short paragraphs is enough. Get to the point quickly."
        )

    async def update_session_prompt(
        self, 
        session_id: str, 
        analysis: Dict[str, Any], 
        context: Dict[str, Any], 
        latest_transcript: str
    ) -> Dict[str, Any]:
        
        system_p = self.build_system_prompt(context)
        user_p = self.build_user_prompt(analysis.get("prediction", ""), latest_transcript)
        
        # Assembled prepared prompt (system prompt + user prompt template)
        prepared_prompt = json.dumps({
            "system_prompt": system_p,
            "user_prompt": user_p
        })
        
        # Load current session cache to preserve other fields
        session_state = await redis_cache.get_session_state(session_id)
        if not session_state:
            session_state = {}
            
        session_state.update({
            "session_id": session_id,
            "state": analysis.get("state", "WAITING"),
            "latest_transcript": latest_transcript,
            "prediction": analysis.get("prediction", ""),
            "prediction_confidence": analysis.get("confidence", 0.0),
            "intent": analysis.get("intent", "General Technical"),
            "category": analysis.get("category", "General Technical"),
            "difficulty": analysis.get("difficulty", "Medium"),
            "technologies": analysis.get("technologies", []),
            "keywords": analysis.get("keywords", []),
            "vector_query": analysis.get("vector_query", ""),
            "resume_context": context.get("resume_context", "None loaded."),
            "knowledge_context": context.get("knowledge_context", "None loaded."),
            "jd_context": context.get("jd_context", "None loaded."),
            "previous_context": context.get("previous_context", "None."),
            "reasoning_focus": context.get("reasoning_focus", "General technical review and validation."),
            "prepared_prompt": prepared_prompt,
            "last_updated": time.time()
        })
        
        # Save to Redis cache
        await redis_cache.set_session_state(session_id, session_state)
        
        # Local memory fallback caching
        if not redis_cache._client:
            redis_cache._local_cache[f"session:{session_id}"] = json.dumps(session_state)
            
        return session_state

prompt_builder = PromptBuilder()
