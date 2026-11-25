from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import logging
from contextlib import asynccontextmanager

from app.core.config import settings
from app.core.logging import setup_logging, logger
from app.cache.redis import redis_cache

# Routers
from app.api.health import router as health_router
from app.api.sessions import router as sessions_router
from app.api.transcripts import router as transcripts_router
from app.api.answers import router as answers_router
from app.api.screenshots import router as screenshots_router
from app.api.resumes import router as resumes_router
from app.api.knowledge import router as knowledge_router
from app.api.job_descriptions import router as jds_router
from app.api.websockets import router as websockets_router
from app.api.auth import router as auth_router
from app.api.mock_interviews import router as mock_interviews_router
from app.api.admins import router as admins_router
from app.api.notifications import router as notifications_router
from app.api.tickets import router as tickets_router
from app.api.platform_health import router as platform_health_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup actions
    setup_logging()
    logger.info("Initializing Roundmate AI backend services...")
    settings.validate_critical_config(fail_fast=(settings.ENVIRONMENT in ("production", "staging")))
    logger.info(f"Config summary: {settings.redacted_summary()}")
    
    # Connect to Redis
    await redis_cache.connect()
    
    # Initialize database tables with fallback support
    from app.db.database import verify_and_initialize_db, close_database_pool
    await verify_and_initialize_db()
    
    yield
    
    # Shutdown actions
    logger.info("Cleaning up backend resources...")
    await redis_cache.disconnect()
    await close_database_pool()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Centralized RFC 7807 Standard Error Handlers
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError
from app.core.errors import (
    APIException,
    api_exception_handler,
    http_exception_handler,
    validation_exception_handler,
    unhandled_exception_handler,
)
from app.core.middleware import EnterpriseGatewayMiddleware

app.add_exception_handler(APIException, api_exception_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

# Enterprise Observability & Gateway Middleware (Trace ID Correlation, Latency Tracking, Tenant Context)
app.add_middleware(EnterpriseGatewayMiddleware)

# Register legacy websockets directly at root for current frontend compatibility
app.include_router(websockets_router)

# Register REST endpoints under both /api (legacy compatibility) and /api/v1 (LLD v1.3 contract)
app.include_router(health_router, tags=["Health"])
_rest_routers = [
    (sessions_router, "Sessions"),
    (transcripts_router, "Transcripts"),
    (answers_router, "Answers"),
    (screenshots_router, "Screenshots"),
    (resumes_router, "Resumes"),
    (knowledge_router, "Knowledge"),
    (jds_router, "Job Descriptions"),
    (mock_interviews_router, "Mock Interview"),
    (auth_router, "Authentication"),
    (admins_router, "Admins"),
    (notifications_router, "Notifications"),
    (tickets_router, "Support Tickets"),
    (platform_health_router, "Platform Health"),
]
for _r, _tag in _rest_routers:
    app.include_router(_r, prefix=settings.API_V1_STR, tags=[_tag])
    if settings.API_V1_STR != "/api/v1":
        app.include_router(_r, prefix="/api/v1", tags=[f"{_tag} v1"])

@app.get("/")
async def root():
    return {
        "message": "Welcome to the Roundmate AI Modular Realtime API",
        "health_check": "/health",
        "documentation": "/docs"
    }
