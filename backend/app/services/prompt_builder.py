import json
import logging
import time
from typing import Dict, Any, Optional

logger = logging.getLogger("copilotx.prompt_builder")

# 1. SPECIALIZED PROMPTS FOR EACH QUESTION TYPE
QUESTION_TYPE_PROMPTS = {
    "behavioral": (
        "QUESTION TYPE: Behavioral\n"
        "RESPONSE FRAMEWORK (STAR Method):\n"
        "- Situation: Set the scene and context in 1 sentence.\n"
        "- Task: Describe your exact responsibility.\n"
        "- Action: Explain the key engineering/leadership steps YOU took.\n"
        "- Result: State the quantified business impact or positive takeaway."
    ),
    "coding": (
        "QUESTION TYPE: Coding (LeetCode-style)\n"
        "RESPONSE FRAMEWORK:\n"
        "1. Algorithmic Intuition: Explain the core idea in 1 sentence.\n"
        "2. Optimal Approach: Describe data structure choices (e.g. Hash Map, Two Pointers, Dynamic Programming).\n"
        "3. Implementation: Provide clean, idiomatic code snippet.\n"
        "4. Complexity: Explicitly state Time Complexity O(...) and Space Complexity O(...)."
    ),
    "experience_check": (
        "QUESTION TYPE: Experience Check\n"
        "RESPONSE FRAMEWORK:\n"
        "- Focus on background verification, relevant past project accomplishments, and sync directly with the Job Description (JD) requirements.\n"
        "- Frame real experience around the exact technologies requested by the interviewer."
    ),
    "how_do_you": (
        "QUESTION TYPE: How-do-you / Engineering Process\n"
        "RESPONSE FRAMEWORK:\n"
        "- Provide a step-by-step practical engineering methodology.\n"
        "- Explain real-world trade-offs, edge cases, and production safety precautions."
    ),
    "situational": (
        "QUESTION TYPE: Situational / Scenario\n"
        "RESPONSE FRAMEWORK:\n"
        "- Assess the hypothetical scenario and identify key risks/constraints.\n"
        "- Present 2 potential approaches and state your decision framework.\n"
        "- Justify your optimal choice based on scalability, maintainability, and team velocity."
    ),
    "system_design": (
        "QUESTION TYPE: System Design\n"
        "RESPONSE FRAMEWORK:\n"
        "1. Clarify Requirements & Constraints (Scale, Throughput, Latency).\n"
        "2. High-Level Architecture (Load Balancer, API Gateway, Services, Storage).\n"
        "3. Data Flow & Storage Choice (Relational vs NoSQL vs Cache).\n"
        "4. Bottlenecks & Mitigation (Scaling, Partitioning, Message Queues, Fault Tolerance)."
    ),
    "technical_knowledge": (
        "QUESTION TYPE: Technical Knowledge\n"
        "RESPONSE FRAMEWORK:\n"
        "- Provide a clear 1-sentence technical definition.\n"
        "- Detail underlying execution mechanics (how it works under the hood).\n"
        "- State core pros, cons, and real-world production use cases."
    ),
    "tell_me_about_yourself": (
        "QUESTION TYPE: Tell Me About Yourself / Elevator Pitch\n"
        "RESPONSE FRAMEWORK:\n"
        "- Deliver a high-impact elevator pitch (Present Role -> Past Achievements -> Why this JD/Company).\n"
        "- Renovate and sync your background directly to highlight the top skills in the Job Description (JD)."
    )
}

# 2. FORMAT ENFORCERS (HIGHEST PRIORITY OVERRIDES)
FORMAT_ENFORCERS = {
    "script_bullets": (
        "CRITICAL FORMAT OVERRIDE: Provide a 1-sentence opening spoken script followed by 3 concise bullet points."
    ),
    "paragraphs": (
        "CRITICAL FORMAT OVERRIDE: Write answer strictly in plain, natural narrative PARAGRAPHS. "
        "Do NOT use bullet points, asterisks, or numbered lists. No headers, no markdown formatting. "
        "Just talk naturally as a confident candidate."
    ),
    "structured_summary": (
        "CRITICAL FORMAT OVERRIDE: Organize answer using bold section headers and structured key takeaways."
    )
}

# 3. LENGTH ENFORCERS
LENGTH_ENFORCERS = {
    "short": "LENGTH REQUIREMENT: Keep answer short and concise (50-100 words / ~30-45 seconds spoken).",
    "concise": "LENGTH REQUIREMENT: Keep answer short and concise (50-100 words / ~30-45 seconds spoken).",
    "balanced": "LENGTH REQUIREMENT: Provide a balanced response (150-250 words / ~1-2 minutes spoken).",
    "detailed": "LENGTH REQUIREMENT: Provide an in-depth response (300-400 words / ~3 minutes spoken).",
    "in_depth": "LENGTH REQUIREMENT: Provide an in-depth response (300-400 words / ~3 minutes spoken)."
}

# 4. TONE ENFORCERS
TONE_ENFORCERS = {
    "formal": "TONE REQUIREMENT: Adopt a formal, executive, and polished professional tone.",
    "conversational": "TONE REQUIREMENT: Adopt a conversational, natural, and engaging spoken tone.",
    "confident_technical": "TONE REQUIREMENT: Adopt an authoritative tone using precise technical engineering terminology."
}

SYSTEM_PROMPT_BASE = """[CONTEXT BLOCK]
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
CRITICAL MANDATES:
1. ALWAYS SYNC AND ALIGN YOUR ANSWERS DIRECTLY TO THE JOB DESCRIPTION (JD) AND RESUME CONTEXT ABOVE.
2. If asked to introduce yourself ("tell me about yourself", "walk me through your resume"), renovate and adapt your self-introduction so that your background directly highlights the key requirements in the Job Description (JD).
"""


class PromptBuilder:
    def __init__(self):
        pass

    def build_system_prompt(self, context: Dict[str, Any], user_preferences: Optional[Dict[str, Any]] = None) -> str:
        base_p = SYSTEM_PROMPT_BASE.format(
            resume_context=context.get("resume_context", "None loaded."),
            jd_context=context.get("jd_context", "None loaded."),
            knowledge_context=context.get("knowledge_context", "None loaded."),
            previous_context=context.get("previous_context", "None."),
        )

        overrides = []

        # Process User Preferences (Question Type, Format, Length, Tone)
        if user_preferences:
            q_type = str(user_preferences.get("question_type", "")).lower().replace(" ", "_").replace("-", "_")
            fmt = str(user_preferences.get("format", "")).lower().replace(" ", "_").replace("+", "_")
            length = str(user_preferences.get("length", "")).lower()
            tone = str(user_preferences.get("tone", "")).lower()

            # Apply Question Type Framework
            if q_type in QUESTION_TYPE_PROMPTS:
                overrides.append(QUESTION_TYPE_PROMPTS[q_type])
            elif "code" in q_type or "leetcode" in q_type:
                overrides.append(QUESTION_TYPE_PROMPTS["coding"])
            elif "system" in q_type or "design" in q_type:
                overrides.append(QUESTION_TYPE_PROMPTS["system_design"])

            # Apply Format Overrides
            if fmt in FORMAT_ENFORCERS:
                overrides.append(FORMAT_ENFORCERS[fmt])
            elif "para" in fmt:
                overrides.append(FORMAT_ENFORCERS["paragraphs"])
            elif "bullet" in fmt or "script" in fmt:
                overrides.append(FORMAT_ENFORCERS["script_bullets"])

            # Apply Length & Tone
            if length in LENGTH_ENFORCERS:
                overrides.append(LENGTH_ENFORCERS[length])
            if tone in TONE_ENFORCERS:
                overrides.append(TONE_ENFORCERS[tone])

        # If no custom format override was provided, default to natural paragraphs
        if not any("CRITICAL FORMAT OVERRIDE" in ov for ov in overrides):
            overrides.append(FORMAT_ENFORCERS["paragraphs"])
        if not any("LENGTH REQUIREMENT" in ov for ov in overrides):
            overrides.append(LENGTH_ENFORCERS["balanced"])

        # Append Override Block at the VERY END for maximum LLM attention weight
        override_block = "\n\n[CRITICAL USER PREFERENCE OVERRIDE - HIGHEST PRIORITY]\n" + "\n".join(f"• {rule}" for rule in overrides) + "\n\nNOTE: The rules in this override block MANDATORY taking precedence over any general system defaults above."

        return base_p + override_block

    def build_user_prompt(self, prediction: str, latest_transcript: str, user_preferences: Optional[Dict[str, Any]] = None) -> str:
        q = prediction if prediction else latest_transcript
        
        prompt = (
            f"Interviewer Question: {q}\n\n"
            "Answer this question the way a real candidate would say it out loud in an interview. "
            "IMPORTANT: Renovate and sync your self-introduction, background, and answer directly to the Job Description (JD) and resume.\n"
        )

        if user_preferences:
            fmt = str(user_preferences.get("format", "")).lower()
            if "para" in fmt:
                prompt += "REMINDER: Write in plain, natural English paragraphs — no bullet points, no asterisks, no numbered lists."
            elif "bullet" in fmt or "script" in fmt:
                prompt += "REMINDER: Provide a 1-sentence opening script followed by concise bullet points."

        return prompt

    async def update_session_prompt(
        self, 
        session_id: str, 
        analysis: Dict[str, Any], 
        context: Dict[str, Any], 
        latest_transcript: str,
        user_preferences: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        
        system_p = self.build_system_prompt(context, user_preferences)
        user_p = self.build_user_prompt(analysis.get("prediction", ""), latest_transcript, user_preferences)
        
        prepared_prompt = json.dumps({
            "system_prompt": system_p,
            "user_prompt": user_p
        })
        
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
            "user_preferences": user_preferences or {},
            "last_updated": time.time()
        })
        
        await redis_cache.set_session_state(session_id, session_state)
        
        if not redis_cache._client:
            redis_cache._local_cache[f"session:{session_id}"] = json.dumps(session_state)
            
        return session_state


prompt_builder = PromptBuilder()
