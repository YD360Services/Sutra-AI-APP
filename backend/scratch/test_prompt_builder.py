import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import time
import json
from app.services.prompt_builder import prompt_builder
from app.cache.redis import redis_cache

async def run_tests():
    print("=== Testing Prompt Builder ===")
    
    # 1. Connect to Redis (will fallback to memory mode if Redis is down)
    await redis_cache.connect()
    
    analysis = {
        "state": "QUESTION_COMPLETED",
        "prediction": "Explain Redis caching strategy, eviction policies, and how to scale it.",
        "confidence": 0.90,
        "intent": "Explain",
        "category": "System Design",
        "difficulty": "Medium",
        "technologies": ["Redis"],
        "keywords": ["cache"],
        "vector_query": "redis cache"
    }
    
    context = {
        "resume_context": "- Built a real-time messaging pipeline using Apache Kafka and Redis.",
        "knowledge_context": "None loaded.",
        "jd_context": "None loaded.",
        "previous_context": "None.",
        "reasoning_focus": "Focus on cache strategy, data consistency, message durability, and system scaling."
    }
    
    session_id = "test-session-uuid-12345"
    latest_transcript = "can you explain redis caching strategy?"
    
    start_time = time.time()
    session_state = await prompt_builder.update_session_prompt(
        session_id=session_id,
        analysis=analysis,
        context=context,
        latest_transcript=latest_transcript
    )
    dur = time.time() - start_time
    
    print(f"Prompt Builder Latency: {dur * 1000:.2f}ms")
    print(f"Stored session state keys: {list(session_state.keys())}")
    
    # Verify Redis read back
    read_start = time.time()
    read_state = await redis_cache.get_session_state(session_id)
    read_dur = time.time() - read_start
    print(f"Redis Read Latency: {read_dur * 1000:.2f}ms")
    
    assert read_state is not None
    assert read_state["state"] == "QUESTION_COMPLETED"
    assert "prepared_prompt" in read_state
    
    # Verify prepared prompt content
    prompt_data = json.loads(read_state["prepared_prompt"])
    assert "Interviewer Question:" in prompt_data["user_prompt"]
    assert "Redis" in prompt_data["user_prompt"]
    
    print("All Prompt Builder tests passed successfully!")
    
    # Cleanup Redis
    if redis_cache._client:
        await redis_cache._client.delete(f"session:{session_id}")
    await redis_cache.disconnect()

if __name__ == "__main__":
    asyncio.run(run_tests())
