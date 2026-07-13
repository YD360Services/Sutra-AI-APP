from fastapi import FastAPI
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup actions
    setup_logging()
    logger.info("Initializing CopilotX backend services...")
    
    # Connect to Redis
    await redis_cache.connect()
    
    # Initialize database tables with fallback support
    from app.db.database import verify_and_initialize_db
    await verify_and_initialize_db()
    
    yield
    
    # Shutdown actions
    logger.info("Cleaning up backend resources...")
    await redis_cache.disconnect()

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

import os
from starlette.middleware.base import BaseHTTPMiddleware
class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        method = request.method
        path = request.url.path
        log_msg = f"Request: {method} {path}"
        if request.query_params:
            log_msg += f" ?{request.query_params}"
        
        os.makedirs("logs", exist_ok=True)
        with open("logs/request_debug.log", "a", encoding="utf-8") as f:
            f.write(f"{log_msg}\n")
        
        try:
            response = await call_next(request)
            with open("logs/request_debug.log", "a", encoding="utf-8") as f:
                f.write(f"Response: {response.status_code}\n")
            return response
        except Exception as e:
            with open("logs/request_debug.log", "a", encoding="utf-8") as f:
                f.write(f"Error: {e}\n")
            raise e

app.add_middleware(RequestLoggingMiddleware)

# Register legacy websockets directly at root for current frontend compatibility
app.include_router(websockets_router)

# Register REST endpoints under the API prefix
app.include_router(health_router, tags=["Health"])
app.include_router(sessions_router, prefix=settings.API_V1_STR, tags=["Sessions"])
app.include_router(transcripts_router, prefix=settings.API_V1_STR, tags=["Transcripts"])
app.include_router(answers_router, prefix=settings.API_V1_STR, tags=["Answers"])
app.include_router(screenshots_router, prefix=settings.API_V1_STR, tags=["Screenshots"])
app.include_router(resumes_router, prefix=settings.API_V1_STR, tags=["Resumes"])
app.include_router(knowledge_router, prefix=settings.API_V1_STR, tags=["Knowledge"])
app.include_router(jds_router, prefix=settings.API_V1_STR, tags=["Job Descriptions"])
app.include_router(mock_interviews_router, prefix=settings.API_V1_STR, tags=["Mock Interview"])
app.include_router(auth_router, prefix=settings.API_V1_STR, tags=["Authentication"])

@app.get("/")
async def root():
    return {
        "message": "Welcome to the CopilotX Modular Realtime API",
        "health_check": "/health",
        "documentation": "/docs"
    }
