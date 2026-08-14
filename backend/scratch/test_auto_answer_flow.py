import asyncio
import sys
import os
import json

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.ai_service import call_llm
from app.services.prompt_builder import PromptBuilder

async def test_auto_answer():
    print("================ AUTO ANSWER PIPELINE TEST ================")
    
    question = "Can you explain how indexes improve database query performance in PostgreSQL?"
    print(f"Testing Question: '{question}'")
    
    builder = PromptBuilder()
    context = {
        "resume_context": "Backend Engineer with 4 years experience in Python, FastAPI, PostgreSQL, and Redis.",
        "jd_context": "Senior Backend Developer - PostgreSQL optimization, microservices, system design.",
        "knowledge_context": "None loaded.",
        "previous_context": "None."
    }
    
    system_prompt = builder.build_system_prompt(context)
    user_prompt = builder.build_user_prompt("", question)
    
    print("\n--- Generating AI Answer via call_llm ---")
    try:
        response = await call_llm(
            prompt=user_prompt,
            system_prompt=system_prompt
        )
        print("✅ Auto Answer Generation SUCCESSFUL!")
        print("\n=== AI RESPONSE PREVIEW ===")
        print(response[:600] if response else "(empty response)")
        return True
    except Exception as e:
        print(f"❌ Auto Answer Generation FAILED: {e}")
        return False

if __name__ == "__main__":
    asyncio.run(test_auto_answer())
