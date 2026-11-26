"""
LLD v1.3 Item 23: Golden evaluation corpus for Indian English accents,
technical vocabulary, acronyms, and low-confidence abstention.
"""
from typing import List, Dict, Any
from app.services.transcript_vocab import correct_transcript_with_confidence

GOLDEN_CORPUS: List[Dict[str, Any]] = [
    {
        "id": "ie-01",
        "raw": "Explain the four pillars of hoops concepts in Java",
        "expected": "Explain the four pillars of OOPS concepts in Java",
        "software": True,
        "confidence": 0.92,
        "should_abstain": False,
    },
    {
        "id": "ie-02",
        "raw": "How do you optimize queries in post grease and no sequel",
        "expected": "How do you optimize queries in PostgreSQL and NoSQL",
        "software": True,
        "confidence": 0.88,
        "should_abstain": False,
    },
    {
        "id": "ie-03",
        "raw": "We deployed microservices on k8s using c i c d and g r p c",
        "expected": "We deployed microservices on Kubernetes using CI/CD and gRPC",
        "software": True,
        "confidence": 0.90,
        "should_abstain": False,
    },
    {
        "id": "ie-04",
        "raw": "In pie thon we use fast api and pie test with j w t",
        "expected": "In Python we use FastAPI and pytest with JWT",
        "software": True,
        "confidence": 0.85,
        "should_abstain": False,
    },
    {
        "id": "fp-01",
        "raw": "Shoot the basketball through the hoops cleanly",
        "expected": "Shoot the basketball through the hoops cleanly",
        "software": False,
        "confidence": 0.95,
        "should_abstain": True,
    },
    {
        "id": "abstain-01",
        "raw": "muffled hoops sequel",
        "expected": "muffled hoops sequel",
        "software": True,
        "confidence": 0.20,
        "should_abstain": True,
    },
]


def evaluate_golden_corpus() -> Dict[str, Any]:
    passed = 0
    failures = []
    for case in GOLDEN_CORPUS:
        res = correct_transcript_with_confidence(
            case["raw"],
            software=case["software"],
            asr_confidence=case["confidence"],
        )
        ok = (
            res["corrected_text"] == case["expected"]
            and res["abstained"] == case["should_abstain"]
        )
        if ok:
            passed += 1
        else:
            failures.append({"id": case["id"], "got": res, "expected": case["expected"]})

    return {
        "total": len(GOLDEN_CORPUS),
        "passed": passed,
        "accuracy": passed / len(GOLDEN_CORPUS) if GOLDEN_CORPUS else 1.0,
        "failures": failures,
    }
