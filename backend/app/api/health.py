"""
System Health, Readiness & Diagnostics Endpoint for RoundMate AI.
Complies with modern API health standards (IETF Health Check Response Format).
"""

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
import time
import os
from typing import Dict, Any

from app.core.config import settings
from app.cache.redis import redis_cache
from app.db.database import AsyncSessionLocal

router = APIRouter()
START_TIME = time.time()


@router.get("/health", tags=["Health"])
@router.get("/healthz", tags=["Health"])
async def health_check():
    """
    Comprehensive system health and readiness check.
    Probes database, in-memory cache, and AI model configurations.
    """
    uptime_seconds = int(time.time() - START_TIME)
    checks: Dict[str, Any] = {}
    is_healthy = True

    # 1. Database Connectivity Probe
    db_status = "unavailable"
    db_latency_ms = None
    try:
        t0 = time.perf_counter()
        async with AsyncSessionLocal() as session:
            from sqlalchemy import text
            await session.execute(text("SELECT 1"))
        db_latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)[:50]}"
        # If DB is down, system is degraded but may serve cached requests
        is_healthy = False

    checks["database"] = {
        "status": db_status,
        "latency_ms": db_latency_ms
    }

    # 2. Cache Layer Probe
    cache_status = "ready"
    try:
        await redis_cache.set("health:probe", "ok", ttl_seconds=10)
        probe_val = await redis_cache.get("health:probe")
        if probe_val != "ok":
            cache_status = "degraded"
    except Exception as ce:
        cache_status = f"error: {str(ce)[:50]}"

    checks["cache"] = {
        "status": cache_status,
        "type": "in_memory_ttl_lru"
    }

    # 3. Upstream AI Providers Config
    checks["ai_providers"] = {
        "gemini_configured": bool(settings.GEMINI_API_KEY and settings.GEMINI_API_KEY.strip()),
        "deepgram_configured": bool(settings.DEEPGRAM_API_KEY and settings.DEEPGRAM_API_KEY.strip()),
        "openai_configured": bool(settings.OPENAI_API_KEY and settings.OPENAI_API_KEY.strip()),
        "groq_configured": bool(settings.GROQ_API_KEY and settings.GROQ_API_KEY.strip()),
        "default_model": settings.GEMINI_MODEL
    }

    response_payload = {
        "status": "healthy" if is_healthy else "degraded",
        "version": "1.0.0",
        "uptime_seconds": uptime_seconds,
        "timestamp": int(time.time()),
        "checks": checks
    }

    status_code = status.HTTP_200_OK if is_healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=status_code, content=response_payload)
