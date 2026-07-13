import sys
sys.path.insert(0, r"c:\Users\omkar\Downloads\copilotx-with-mock-interview\copilotx-live-demo-product\backend")

import asyncio
import time
from app.services.transcript_intelligence import transcript_engine, SessionState

async def run_tests():
    print("=== Testing Transcript Intelligence Engine ===")
    
    # 1. Test standard question started
    t1 = "Can you explain Redis caching strategy?"
    start_time = time.time()
    res1 = transcript_engine.analyze(t1, previous_state=SessionState.WAITING, pause_duration=0.0)
    dur = time.time() - start_time
    
    print(f"Analysis Latency: {dur * 1000:.2f}ms")
    print(f"State: {res1['state']}")
    print(f"Question Started: {res1['question_started']}")
    print(f"Question Completed: {res1['question_completed']}")
    print(f"Intent: {res1['intent']}")
    print(f"Category: {res1['category']}")
    print(f"Technologies: {res1['technologies']}")
    print(f"Keywords: {res1['keywords']}")
    print(f"Prediction: {res1['prediction']}")
    print(f"Confidence: {res1['confidence']}")
    print(f"Vector Query: {res1['vector_query']}")
    print(f"Difficulty: {res1['difficulty']}\n")
    
    assert res1['state'] == SessionState.QUESTION_COMPLETED
    assert res1['question_started'] is True
    assert "Redis" in res1['technologies']
    assert "cache" in res1['keywords']
    assert res1['category'] == "System Design"
    assert res1['intent'] == "Explain"
    assert dur < 0.005, "Latency exceeded 5ms target!"
 
    # 2. Test incremental building
    t2 = "What is the difference between rank and dense_rank"
    res2 = transcript_engine.analyze(t2, previous_state=SessionState.QUESTION_STARTED, pause_duration=0.0)
    print(f"Incremental Segment: '{t2}'")
    print(f"State: {res2['state']}")
    print(f"Prediction: {res2['prediction']}")
    print(f"Confidence: {res2['confidence']}\n")
    
    assert res2['state'] == SessionState.QUESTION_BUILDING
    assert res2['intent'] == "Compare"
    assert "RANK()" in res2['prediction']  # Matches SQL RANK prediction

    print("All Transcript Intelligence Engine tests passed successfully!")

if __name__ == "__main__":
    asyncio.run(run_tests())
