import sys
sys.path.insert(0, r"c:\Users\omkar\Downloads\copilotx-with-mock-interview\copilotx-live-demo-product\backend")

import asyncio
import time
from sqlalchemy import select
from app.db.database import get_db
from app.services.context_orchestrator import context_orchestrator, rank_and_truncate_text

async def run_tests():
    print("=== Testing Context Orchestrator ===")
    
    # 1. Test deterministic bullet ranking
    resume_text = """
    - Built a real-time messaging pipeline using Apache Kafka and Redis.
    - Handled database migration from Oracle to PostgreSQL.
    - Optimized React frontend load times by 40% with code splitting.
    - Developed Java APIs with Spring Boot.
    """
    
    keywords = ["Redis", "Kafka", "cache"]
    start_time = time.time()
    bullets = rank_and_truncate_text(resume_text, keywords, max_items=3)
    dur = time.time() - start_time
    
    print(f"Ranking duration: {dur * 1000:.2f}ms")
    print(f"Ranked bullets: {bullets}")
    
    assert len(bullets) <= 3
    assert "Kafka" in bullets[0]
    assert "PostgreSQL" in bullets[1] or "React" in bullets[1]
    
    # 2. Test fetching context from DB/cache fallback
    async for db in get_db():
        # Check active session fetch works or falls back correctly
        c_start = time.time()
        context = await context_orchestrator.prepare_context(
            session_id=None,
            user_id=None,
            db=db,
            keywords=keywords,
            technologies=["Redis", "Kafka"],
            resume_content=resume_text,
            knowledge_content="prompt_id:test-id|doc_id:test-doc-id"
        )
        c_dur = time.time() - c_start
        print(f"Orchestration Latency: {c_dur * 1000:.2f}ms")
        print(f"Resume Context: {context['resume_context']}")
        print(f"JD Context: {context['jd_context']}")
        print(f"Knowledge Context: {context['knowledge_context']}")
        print(f"Reasoning Focus: {context['reasoning_focus']}")
        
        assert "Kafka" in context['resume_context']
        assert context['reasoning_focus'] != ""
        break

    print("All Context Orchestrator tests passed successfully!")

if __name__ == "__main__":
    asyncio.run(run_tests())
