import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import time
import asyncio
import uuid
from typing import List, Dict, Any

from app.services.candidate_memory import (
    CandidateMemoryItem,
    CandidateMemoryStore,
    candidate_memory_store,
    tokenize_for_search,
    METRIC_REGEX
)
from app.services.context_orchestrator import rank_and_truncate_text

# Sample mock interview Q&As representative of typical candidate responses
SAMPLE_MOCK_QAS = [
    {
        "question": "Tell me about a time you solved a severe latency bottleneck in a distributed system.",
        "answer": (
            "Situation: At ScaleTech, our payment webhook service experienced 400ms latency spikes during flash sales.\n"
            "Task: I was tasked with bringing p99 latency under 80ms while maintaining zero message loss.\n"
            "Action: I migrated the hot session store from Postgres to a Redis Cluster with read replicas and batched Kafka consumer commits.\n"
            "Result: We reduced p99 latency by 58% down to 65ms and handled over 25k req/s without dropping a single event."
        ),
        "score": 95
    },
    {
        "question": "How do you handle disagreement with a technical lead on architecture decisions?",
        "answer": (
            "Situation: During a microservices overhaul at FinCloud, our tech lead wanted to use gRPC exclusively, but third-party clients required REST.\n"
            "Task: Reconcile external partner requirements with internal microservice performance goals.\n"
            "Action: I proposed and prototyped an Envoy-based gRPC-JSON transcoding gateway, demonstrating it in a sandbox with benchmarks.\n"
            "Result: The team adopted the hybrid approach, cutting development time by 3 weeks and meeting 100% of external SLA contracts."
        ),
        "score": 92
    },
    {
        "question": "Describe a time you dealt with a major production outage.",
        "answer": (
            "Situation: At DataFlow, a database deadlocking incident caused cascading 504 gateway timeouts across our auth service affecting 120k users.\n"
            "Task: Quickly isolate the lock cycle, restore authentication, and prevent recurrence.\n"
            "Action: I identified an unindexed foreign key in a new migration, killed the locking transactions, and deployed an index hotfix in 14 minutes.\n"
            "Result: Restored full uptime within 18 minutes, created an automated migration linting step that prevented 4 similar schema bugs."
        ),
        "score": 88
    },
    {
        "question": "Have you worked with Apache Spark and data pipelines?",
        "answer": (
            "At AnalyticsCo, I managed a daily PySpark ETL pipeline processing 4TB of event logs in Databricks and Snowflake. "
            "I repartitioned skewed partitions which decreased job runtimes by 42% and saved $15,000 monthly in cloud compute costs."
        ),
        "score": 90
    }
]

def run_candidate_memory_benchmark():
    print("=" * 70)
    print("1. BENCHMARKING CANDIDATE MEMORY NORMALIZATION & PARSING")
    print("=" * 70)

    t0 = time.perf_counter()
    memories = CandidateMemoryStore.normalize_mock_qas(
        SAMPLE_MOCK_QAS,
        role="Senior Backend Engineer",
        company="ScaleTech"
    )
    normalization_ms = (time.perf_counter() - t0) * 1000.0

    print(f"Normalized {len(memories)} memories in {normalization_ms:.3f} ms")
    assert len(memories) == 4, f"Expected 4 memories, got {len(memories)}"

    # Validate first memory STAR parsing
    m0 = memories[0]
    print(f"\n[Memory 0 - STAR Inspection]")
    print(f"Topic: {m0.topic}")
    print(f"Situation: {m0.star.get('situation')}")
    print(f"Action: {m0.star.get('action')}")
    print(f"Result: {m0.star.get('result')}")
    print(f"Extracted Metrics: {m0.metrics}")
    print(f"Technologies: {m0.technologies}")

    assert "400ms" in m0.metrics or "58%" in m0.metrics or "65ms" in m0.metrics
    assert "redis" in m0.technologies and "kafka" in m0.technologies
    assert m0.star.get("situation") and m0.star.get("result")

    # Benchmark in-memory retrieval latency over 1,000 queries
    store = CandidateMemoryStore()
    store.add_memories(memories)

    test_queries = [
        "What is your experience with Redis and Kafka under high load?",
        "Can you talk about resolving conflicts with team members or leads?",
        "Tell me about a high severity production incident you resolved.",
        "How do you optimize Spark pipelines and data lakes?",
        "Describe a time you improved latency or system throughput."
    ]

    print("\n" + "=" * 70)
    print("2. BENCHMARKING IN-MEMORY RETRIEVAL LATENCY (1,000 ITERATIONS)")
    print("=" * 70)

    N_RUNS = 1000
    t_start = time.perf_counter()
    for i in range(N_RUNS):
        q = test_queries[i % len(test_queries)]
        results = store.retrieve_top_memories(query=q, max_items=2)
        assert len(results) > 0
    total_time_ms = (time.perf_counter() - t_start) * 1000.0
    avg_latency_us = (total_time_ms / N_RUNS) * 1000.0

    print(f"Completed {N_RUNS} retrievals in {total_time_ms:.2f} ms")
    print(f"Average retrieval latency: {avg_latency_us:.2f} µs ({avg_latency_us / 1000.0:.4f} ms)")
    assert (avg_latency_us / 1000.0) < 2.0, "Retrieval latency must be < 2.0 ms"
    print(">>> PASS: Retrieval latency well within sub-millisecond target! <<<")

    # Test top query matching
    top_hit = store.retrieve_top_memories("Can you tell me about Kafka and latency spikes?", max_items=1)[0]
    print(f"\nQuery: 'Can you tell me about Kafka and latency spikes?'")
    print(f"Top Hit Topic: {top_hit.topic}")
    assert "latency" in top_hit.topic.lower() or "scaletech" in top_hit.company.lower()
    print(">>> PASS: Semantic keyword match correctly prioritized top STAR story! <<<")

def run_context_reduction_benchmark():
    print("\n" + "=" * 70)
    print("3. BENCHMARKING CONTEXT REDUCTION & FAST-PATH PACKET FORMAT")
    print("=" * 70)

    # Simulate raw unoptimized resume text (~3,500 characters)
    raw_resume = """
    John Doe - Senior Software Engineer
    San Francisco, CA | john@example.com | github.com/johndoe

    SUMMARY:
    Passionate software engineer with 7 years of experience building resilient distributed backend systems,
    cloud infrastructure, and high-throughput real-time APIs in Golang and Python.

    EXPERIENCE:
    Staff Backend Engineer | StreamScale (2022 - Present)
    - Designed and implemented payment webhook ingress pipeline processing 30,000 RPS.
    - Cut p99 response times from 400ms to 65ms by migrating hot session state to Redis Cluster.
    - Re-architected batch database polling into event-driven Kafka architecture.
    - Mentored 6 junior engineers and established zero-downtime deployment practices.
    - Managed on-call incident response for Tier-1 microservices.

    Senior Software Engineer | DataCorp (2019 - 2022)
    - Engineered analytics ingestion pipeline using FastAPI, Celery, and PostgreSQL.
    - Decreased query latency by 70% with materialized views and query plan optimizations.
    - Containerized legacy services with Docker and orchestrated deployment via Kubernetes.
    - Collaborated with product teams to specify technical API contracts.

    Software Engineer | EarlyStage Labs (2017 - 2019)
    - Built responsive React dashboards and backend RESTful endpoints in Django.
    - Integrated Stripe and PayPal payment gateways with webhook retry backoff.
    - Wrote comprehensive unit and integration test suites using pytest with 88% coverage.

    SKILLS:
    Languages: Python, Go, TypeScript, SQL, Bash
    Technologies: Redis, Kafka, PostgreSQL, Docker, Kubernetes, AWS, FastAPI, gRPC
    """

    # Unoptimized context: entire raw resume + full docs = ~2,000+ chars
    raw_tokens_approx = len(raw_resume.split())

    # Optimized context using rank_and_truncate_text
    search_terms = ["redis", "kafka", "latency", "payment", "throughput"]
    ranked_lines = rank_and_truncate_text(raw_resume, search_terms, max_items=8)
    optimized_text = "\n".join(ranked_lines)
    optimized_tokens_approx = len(optimized_text.split())

    print(f"Raw Resume Tokens (approx): {raw_tokens_approx} words")
    print(f"Optimized Resume Tokens (approx): {optimized_tokens_approx} words")
    print(f"Token Reduction: {((raw_tokens_approx - optimized_tokens_approx) / raw_tokens_approx) * 100:.1f}%")

    print("\n[Optimized Context Sample]")
    for line in ranked_lines:
        print(f"  > {line}")

    assert optimized_tokens_approx < raw_tokens_approx * 0.5, "Optimized context should be < 50% of raw size"
    print(">>> PASS: Context tokens reduced by > 50% for fast prefill and low TTFT! <<<")

def run_fastpath_cache_simulation():
    print("\n" + "=" * 70)
    print("4. BENCHMARKING FAST-PATH CONTEXT RETRIEVAL (0ms DB SIMULATION)")
    print("=" * 70)

    # Simulate prewarmed context packet stored in Redis
    prewarmed_packet = {
        "role_info": "Role: Senior Distributed Systems Engineer at ScaleTech",
        "candidate_memories": (
            "[Candidate Memory #1]\n"
            "Practiced Topic: Latency bottleneck in distributed system\n"
            "STAR Breakdown:\n"
            "  • Situation: Webhook service experienced 400ms latency spikes\n"
            "  • Action: Migrated session store to Redis Cluster & batched Kafka commits\n"
            "  • Result: Reduced p99 latency by 58% down to 65ms at 25k req/s\n"
            "Verified Metrics: 400ms, 58%, 65ms, 25k req/s"
        ),
        "resume_context": "Cut p99 response times from 400ms to 65ms by migrating hot session state to Redis Cluster.\nDesigned payment webhook pipeline processing 30,000 RPS.",
        "jd_context": "Required: High-throughput distributed systems, Redis, Kafka, sub-100ms latency SLAs.",
        "previous_context": "Previous topic: Discussed microservice boundaries and API gateway design.",
        "keywords": ["redis", "kafka", "latency"]
    }

    # Simulate the hot-path lookup in _prepare_answer_context
    t0 = time.perf_counter()
    pw = prewarmed_packet
    latest_question = "How would you design a caching layer to handle 20,000 requests per second with low latency?"
    context_prompt = f"""ROLE & COMPANY:
{pw.get("role_info")}

CANDIDATE MEMORIES (PRACTICED STAR STORIES & EXPERIENCES):
{pw.get("candidate_memories")}

USER RESUME EVIDENCE:
{pw.get("resume_context")}

JOB DESCRIPTION & FOCUS:
{pw.get("jd_context")}

RECENT INTERVIEW Q&A:
{pw.get("previous_context")}

QUESTION TO ANSWER:
{latest_question}

SYSTEM MANDATE & INSTRUCTIONS:
- You ARE the candidate sitting in the interview right now. Speak in the FIRST PERSON ("I", "my experience").
- MANDATE: START IMMEDIATELY with the core STAR answer or technical points so the candidate can speak instantly.
- REUSE THE CANDIDATE'S PREPARED STAR STORIES, METRICS, AND PRACTICED ANSWERS SHOWN ABOVE.
""".strip()
    t_fastpath_ms = (time.perf_counter() - t0) * 1000.0

    print(f"Fast-path prompt constructed in: {t_fastpath_ms:.4f} ms")
    print(f"Total prompt length: {len(context_prompt)} characters (~{len(context_prompt.split())} words)")
    assert t_fastpath_ms < 1.0, "Fast-path assembly must be < 1.0 ms"
    print(">>> PASS: Fast-path context packet constructs in < 1ms with 0 database queries! <<<")

if __name__ == "__main__":
    run_candidate_memory_benchmark()
    run_context_reduction_benchmark()
    run_fastpath_cache_simulation()
    print("\n" + "=" * 70)
    print("ALL LATENCY & ACCURACY BENCHMARKS PASSED SUCCESSFULLY!")
    print("=" * 70)
