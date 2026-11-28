# RoundMate AI — Production Runbooks & Staged Render Cutover (LLD v1.3 Items 67 & 68)

## 1. Deployment & Rollback Runbook
- **Blueprint:** `render.yaml` deploys `roundmate-backend` (Web), `roundmate-bg-worker` (Worker), `roundmate-valkey` (Key Value), and `roundmate-db` (PostgreSQL 16).
- **Pre-Deploy Check:** Run `alembic upgrade head` against staging snapshot before promoting to production.
- **Rollback Procedure:**
  1. Trigger Render Dashboard rollback to previous known-good commit SHA.
  2. If schema migration `0002` caused incompatibility, run `alembic downgrade 0001` (verified reversible).
  3. Drain active WebSocket connections cleanly via `graceful_drain_websockets()`.

## 2. Database Backup & Restore Verification
- **Automated Snapshots:** Render Managed PostgreSQL daily automated backups + PITR.
- **Monthly Drill:** Restore snapshot to isolated staging DB, verify `pgvector` extension (`SELECT * FROM pg_extension WHERE extname='vector';`), and validate row counts across `users`, `sessions`, and `conversation_turns`.

## 3. Valkey (Key Value) Recovery & Degraded Mode
- **Failure Impact:** If Valkey becomes unreachable, `RedisCache` degrades to per-worker in-memory `_TTLLRUCache`; PostgreSQL remains authoritative for durable history.
- **Recovery:** Re-establish `REDIS_URL` private connection; active sessions increment `SessionFenceManager` epoch on client reconnect.

## 4. Provider Outage & Queue Replay
- **LLM 429 / 5xx:** `ModelRegistry` automatically isolates 429 cooldowns per `(provider, model)` and trips provider circuit breaker after 3 consecutive 5xx errors.
- **Background Queue Replay:** Inspect `_failed_jobs` / `rm:arq:jobs` dead-letter queue and invoke `job_queue.replay_failed_job(job_id)`.

## 5. Privacy Deletion Verification
- Enqueue `privacy_deletion` job with `tenant_id`, `user_id`, and `storage_key`.
- Confirm deletion across: (1) S3/R2 object storage, (2) `semantic_embeddings` & `source_provenance` tables, and (3) Valkey session/evidence keys.

## 6. Staged Render Cutover Checklist
1. Provision `roundmate-db` (PostgreSQL 16 + `pgvector`) & `roundmate-valkey` in same region (`oregon`).
2. Configure environment group secrets (`JWT_SECRET`, `DEEPGRAM_API_KEY`, `S3_BUCKET`, `OTEL_EXPORTER_OTLP_ENDPOINT`).
3. Deploy Web Service & Background Worker; verify `/health` and `/api/v1` OpenAPI schema.
4. Execute WebSocket ticket handshake, ASR audio, provisional-to-final promotion, and reconnect smoke tests before shifting 100% traffic.
