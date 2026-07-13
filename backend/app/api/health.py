from fastapi import APIRouter
from app.core.config import settings

router = APIRouter()

@router.get("/health")
async def health():
    return {
        "status": "ok",
        "gemini_configured": bool(settings.GEMINI_API_KEY and settings.GEMINI_API_KEY.strip()),
        "deepgram_configured": bool(settings.DEEPGRAM_API_KEY and settings.DEEPGRAM_API_KEY.strip()),
        "openai_configured": bool(settings.OPENAI_API_KEY and settings.OPENAI_API_KEY.strip()),
        "default_model": settings.GEMINI_MODEL,
        "models_available": {
            "gemini": [
                "gemini-2.5-flash",
                "gemini-2.5-pro",
                "gemini-2.0-flash",
            ],
            "openai": [
                "gpt-4.5-preview",
                "gpt-4o",
                "gpt-4o-mini",
                "gpt-4-turbo",
                "gpt-4",
                "gpt-3.5-turbo",
            ] if settings.OPENAI_API_KEY else []
        }
    }
