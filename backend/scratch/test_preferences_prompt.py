import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.services.prompt_builder import prompt_builder

def test_prompt_builder_preferences():
    sample_context = {
        "resume_context": "Senior Software Engineer with 5+ years experience in Python, FastAPI, and PostgreSQL.",
        "jd_context": "Senior Backend Developer responsible for microservices and scalable API design.",
        "knowledge_context": "Redis caching and PostgreSQL optimization docs.",
        "previous_context": "Discussed system performance."
    }

    # Test 1: Behavioral + Paragraphs
    p1 = prompt_builder.build_system_prompt(sample_context, {
        "question_type": "behavioral",
        "format": "paragraphs",
        "length": "balanced",
        "tone": "formal"
    })
    assert "STAR Method" in p1
    assert "CRITICAL FORMAT OVERRIDE: Write answer strictly in plain, natural narrative PARAGRAPHS" in p1
    print("[PASSED] Test 1 (Behavioral + Paragraphs)")

    # Test 2: Coding + Script Bullets
    p2 = prompt_builder.build_system_prompt(sample_context, {
        "question_type": "coding",
        "format": "script_bullets",
        "length": "short",
        "tone": "confident_technical"
    })
    assert "LeetCode-style" in p2
    assert "CRITICAL FORMAT OVERRIDE: Provide a 1-sentence opening spoken script followed by 3 concise bullet points" in p2
    print("[PASSED] Test 2 (Coding + Script Bullets)")

    # Test 3: System Design + Structured Summary
    p3 = prompt_builder.build_system_prompt(sample_context, {
        "question_type": "system_design",
        "format": "structured_summary",
        "length": "detailed",
        "tone": "conversational"
    })
    assert "System Design" in p3
    assert "High-Level Architecture" in p3
    assert "CRITICAL FORMAT OVERRIDE: Organize answer using bold section headers" in p3
    print("[PASSED] Test 3 (System Design + Structured Summary)")

    print("\nALL 3 PROMPT ENGINE TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_prompt_builder_preferences()
