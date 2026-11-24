# RoundMate Production Architecture v2.0 - Frozen Baseline & Engineering Sign-Off

**Document Status:** Approved & Frozen Baseline  
**Architecture Version:** 2.0  
**Target:** Modular Monolith with Distributed State Safety  

---

## 1. Executive Summary & Design Baseline
RoundMate AI Architecture v2.0 transitions the platform from fragmented HTTP SSE/REST streaming to a unified real-time WebSocket duplex gateway (`/ws/transcribe/{session_id}`). The core architecture adheres to a clean 13-package modular monolith supported by distributed cache primitives (Redis), high-durability relational storage with hybrid search (PostgreSQL with `pgvector` + Full-Text Search), and dedicated background workers for heavy I/O operations.

```
       [ Client Frontend ]
               |
    WebSocket (Duplex) / HTTPS REST
               v
    +-----------------------------------------------------------------+
    | RoundMate Backend Modular Monolith (FastAPI / ASGI)             |
    |                                                                 |
    |  +------------------------+      +---------------------------+  |
    |  | Ingress & Security     | ---> | Live Session Gateway      |  |
    |  | (JWT Auth, Tracing, RL)|      | (Single-Writer Outbound Q)|  |
    |  +------------------------+      +---------------------------+  |
    |                                                |                |
    |                                                v                |
    |  +-------------------------+     +---------------------------+  |
    |  | Dynamic Model Router    | <---| Predictive Engine         |  |
    |  | (429 Isolation, Breaker)|     | (Fast Speculative Engine) |  |
    |  +-------------------------+     +---------------------------+  |
    |               |                                |                |
    |               v                                v                |
    |  +-------------------------+     +---------------------------+  |
    |  | Context Orchestrator    |     | Hybrid RAG & FTS Service  |  |
    |  | (Rolling Summary + Fact)|     | (PostgreSQL + pgvector)   |  |
    |  +-------------------------+     +---------------------------+  |
    +-----------------------------------------------------------------+
               |                                     |
               v                                     v
    [ Redis: Atomic State & Locks ]       [ PostgreSQL + pgvector ]
```

---

## 2. The Four Implementation Clarifications

### Clarification 1: Separation of Vector and Full-Text Search (FTS) Indexes
Semantic embeddings and lexical search serve complementary retrieval paths and must be stored in specialized PostgreSQL columns with isolated index strategies:
* **Vector Embeddings (`embedding vector(768)`):** Indexed via **HNSW** (`m=16`, `ef_construction=64`, distance metric `vector_cosine_ops`) or **IVFFlat**.
* **Lexical Full-Text Search (`search_tsvector tsvector`):** Generated using `to_tsvector('english', ...)` and indexed via **GIN** (`gin(search_tsvector)`).
* **Isolation Guarantee:** FTS fallback operates strictly independently of external embedding provider availability. If embedding generation fails, times out, or returns empty candidates, lexical GIN FTS provides immediate fallback without degrading latency.

### Clarification 2: Multi-Worker Atomic Generation Guard
In production environments running multiple Uvicorn/Gunicorn worker processes:
* In-memory dictionaries (`_generation_versions = {}`) fail to coordinate cancellation across worker process boundaries.
* **Shared Redis State:** Active generation versions are maintained under Redis keys `gen_ver:{session_id}` with atomic `INCR` operations.
* **Dual-Check Invariant:** 
  1. Workers check `current_ver == get_generation_version(session_id)` prior to yielding any streaming token.
  2. Workers perform a final verification before committing question-answer pairs (`_persist_qa`) into PostgreSQL. Stale tokens and cancelled turns are discarded without mutating the database.

### Clarification 3: Queue Failure Policy & Envelope Contract
All WebSocket outbound frames pass through a dedicated single-writer queue (`asyncio.Queue(maxsize=150)`).
* **Protocol Envelope Contract:**
  ```json
  {
    "protocol_version": "2.0",
    "event_id": "evt-uuid-v4",
    "event": "answer.chunk",
    "session_id": "sess-uuid",
    "answer_id": "ans-uuid",
    "prediction_id": "pred-uuid",
    "generation_version": 3,
    "seq": 14,
    "text": " token string ",
    "timestamp": 1711200000.123
  }
  ```
* **Backpressure & Drop Policy:** If the outbound queue exceeds its bound (`maxsize=150`), transient interim events (`transcript.interim`) are shed to preserve line-rate delivery for mission-critical events (`answer.chunk`, `prediction.cancel`).

### Clarification 4: Explicit Audio Frame Protocol Definition
To eliminate client-side ambiguity regarding real-time transcription:
* **Audio Format:** Linear PCM, 16,000 Hz, 16-bit signed little-endian (`s16le`), mono channel.
* **Frame Size & Cadence:** 250ms chunks (4,000 samples = 8,000 bytes per frame).
* **Wire Protocol:** Raw binary frames over WebSocket for audio payload; JSON text frames reserved for session control, bidirectional transcripts, predictions, and streamed answers.

---

## 3. Invariants & Security
1. **WebSocket Handshake Authentication:** Connection requests must supply a valid JWT bearer token either via the HTTP `Authorization` header during the handshake or via query parameters where browser WebSocket constraints prevent header injection. Unauthenticated handshakes terminate with close code `4401`.
2. **Deterministic Model Routing:** Rate-limit events (HTTP 429) trigger isolated cooldowns strictly for the `(provider, model)` pair respecting upstream `Retry-After`. Provider circuit breakers trip only after 3 consecutive 5xx/connection outages across all models belonging to that provider.

---

## 4. Next Engineering Milestones
1. **Load & Concurrency Stress Testing:** Validate 200+ concurrent audio + answer streaming sessions with synthetic network jitter.
2. **Distributed Redis Pub/Sub Outbound Gateway:** Enable horizontal autoscaling of ASGI processes where WebSocket subscribers and LLM generation workers reside on different nodes.
3. **Automated Migration Suite:** Deploy Alembic migration scripts establishing the GIN and HNSW indices on RDS/Cloud SQL PostgreSQL.
4. **End-to-End Client SDK Validation:** Validate Electron/Web client reconnection, backoff, and generation race cancellation under simulated packet loss.
