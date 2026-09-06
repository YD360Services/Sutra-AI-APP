from google import genai
from google.genai import types
from app.core.config import settings
from app.services.ai_service import (
    genai_client,
    openai_client,
    log_prompt,
    log_response,
)
import logging
import base64

logger = logging.getLogger("copilotx.screenshot_service")

def _pick_vision_model(preferred_model: str = None):
    """
    Pick the best vision-capable model.
    Priority:
      1. If OpenAI key is available → use gpt-4o-mini (fast, cheap, vision-capable)
      2. If Gemini key is available → use gemini-2.0-flash (vision-capable)
      3. Raise so the endpoint can return a clear error.
    """
    model_lower = (preferred_model or "").lower()

    # If the user explicitly requested a Gemini model, honour it (Gemini has vision)
    if "gemini" in model_lower and settings.GEMINI_API_KEY and genai_client:
        return ("gemini", "gemini-3.6-flash")

    # OpenAI vision path (maps gpt-5.5 / gpt-5.5-mini / gpt-5.6 -> gpt-4o-mini or gpt-4o)
    if settings.OPENAI_API_KEY and openai_client:
        if preferred_model and any(k in model_lower for k in ["5.6", "4o", "heavy", "pro"]):
            return ("openai", "gpt-4o")
        return ("openai", "gpt-4o-mini")

    # Fallback to Gemini even if the user didn't ask for it
    if settings.GEMINI_API_KEY and genai_client:
        return ("gemini", "gemini-3.6-flash")

    raise ValueError(
        "No vision-capable API key configured. "
        "Add OPEN_API_KEY (OpenAI) or GEMINI_API_KEY to backend/.env"
    )


async def analyze_screenshot(image_bytes: bytes, system_prompt: str, model: str = None) -> str:
    """Analyze a screenshot image and return a JSON string with 'question' and 'answer' keys."""

    try:
        provider, vision_model = _pick_vision_model(model)
    except ValueError as e:
        logger.error(f"[Screenshot] No vision model available: {e}")
        return f'{{"question":"Screenshot Question","answer":"No vision-capable API key configured. Add OPEN_API_KEY or GEMINI_API_KEY to backend/.env"}}'

    logger.info(f"[Screenshot] Using {provider} / {vision_model} (requested: {model!r})")

    # ── OpenAI Vision ──────────────────────────────────────────────────────────
    if provider == "openai":
        try:
            b64 = base64.b64encode(image_bytes).decode("utf-8")
            log_file = log_prompt(
                provider="openai",
                model=vision_model,
                system_prompt=system_prompt,
                user_prompt="(vision: screenshot image)",
                response_json=True,
                temperature=0.3,
                stream=False,
            )
            response = await openai_client.chat.completions.create(
                model=vision_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Answer the question visible in this screenshot."},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                        ],
                    },
                ],
                temperature=0.3,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or ""
            if log_file:
                log_response(log_file, response_text=content)
            logger.info(f"[Screenshot] OpenAI response length: {len(content)}")
            return content
        except Exception as e:
            logger.error(f"[Screenshot] OpenAI Vision error: {e}")
            # Fall through to Gemini if available
            if not (settings.GEMINI_API_KEY and genai_client):
                return f'{{"question":"Screenshot Question","answer":"Error analyzing screenshot via OpenAI: {str(e)}"}}'
            logger.info("[Screenshot] Falling back to Gemini vision...")
            provider = "gemini"
            vision_model = settings.GEMINI_MODEL or "gemini-3.6-flash"

    # ── Gemini Vision ──────────────────────────────────────────────────────────
    if provider == "gemini":
        if not genai_client:
            logger.warning("[Screenshot] Gemini Client not configured for vision analysis.")
            return '{"question":"Screenshot received","answer":"Add GEMINI_API_KEY in backend/.env to enable Gemini vision analysis."}'
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            log_file = log_prompt(
                provider="gemini",
                model=vision_model,
                system_prompt=system_prompt,
                user_prompt="(vision: screenshot image)",
                response_json=True,
                temperature=0.3,
                stream=False,
            )
            response = await loop.run_in_executor(
                None,
                lambda: genai_client.models.generate_content(
                    model=vision_model,
                    contents=[
                        types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                        "Answer the question visible in this screenshot.",
                    ],
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=0.3,
                        response_mime_type="application/json",
                    ),
                ),
            )
            content = response.text or ""
            if log_file:
                log_response(log_file, response_text=content)
            logger.info(f"[Screenshot] Gemini response length: {len(content)}")
            return content
        except Exception as e:
            logger.error(f"[Screenshot] Gemini Vision error: {e}")
            return f'{{"question":"Screenshot Question","answer":"Error analyzing screenshot: {str(e)}"}}'

    return '{"question":"Screenshot Question","answer":"No vision provider matched."}'
