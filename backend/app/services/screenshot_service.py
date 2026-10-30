from google import genai
from google.genai import types
from app.core.config import settings
from app.services.ai_service import (
    get_gemini_client,
    get_openai_client,
    log_prompt,
    log_response,
)
import logging
import base64

logger = logging.getLogger("roundmate.screenshot_service")

def _pick_vision_model(preferred_model: str = None):
    """
    Pick the best vision-capable model for screenshots.
    Priority:
      1. If OpenAI client is available and Astra/GPT-6/GPT-5.6/GPT-4o or default is requested -> use gpt-4o (flagship OCR and vision reasoning)
      2. If Gemini was requested or Gemini client is available -> use dedicated vision model (gemini-2.5-pro / gemini-2.5-flash / gemini-3.7-flash, avoiding TTS voice models)
      3. Otherwise raise ValueError with setup instructions.
    """
    model_lower = (preferred_model or "").lower()
    gemini_client = get_gemini_client()
    openai_client = get_openai_client()

    # Determine dedicated Gemini vision model (avoid TTS voice models for screenshot OCR)
    gemini_model = "gemini-3.8-flash"
    if "argon" in model_lower or "4-argon" in model_lower:
        gemini_model = "gemini-3.8-flash"
    elif "3.5" in model_lower or "3.8" in model_lower or "flash-lite" in model_lower:
        gemini_model = "gemini-3.5-flash-lite"
    elif "flash" in model_lower:
        gemini_model = "gemini-3.8-flash"

    # If OpenAI client is available:
    # Use backend models directly as target providers for vision
    if openai_client:
        if "luna" in model_lower:
            return ("openai", "gpt-6-luna", openai_client)
        if any(k in model_lower for k in ["terra", "astra"]):
            return ("openai", "gpt-6.1-terra", openai_client)
        if "sol" in model_lower:
            return ("openai", "gpt-6-luna", openai_client)
        if any(k in model_lower for k in ["heavy", "pro"]):
            return ("openai", "gpt-6.1-terra", openai_client)
        if not preferred_model:
            return ("openai", "gpt-6.1-terra", openai_client)
        elif not any(k in model_lower for k in ["gemini", "claude"]):
            return ("openai", "gpt-6-luna", openai_client)

    # If the user explicitly requested a Gemini model, honour with dedicated Gemini vision model
    if "gemini" in model_lower and gemini_client:
        return ("gemini", gemini_model, gemini_client)

    # If OpenAI client is available
    if openai_client:
        return ("openai", "gpt-4o", openai_client)

    # Fallback to Gemini if available
    if gemini_client:
        return ("gemini", gemini_model, gemini_client)

    raise ValueError(
        "No vision-capable API key configured. "
        "Add GEMINI_API_KEY or OPENAI_API_KEY to your environment variables."
    )
async def analyze_screenshot(image_bytes: bytes, system_prompt: str, model: str = None) -> str:
    """Analyze a screenshot image and return a JSON string with 'question' and 'answer' keys."""

    try:
        provider, vision_model, client = _pick_vision_model(model)
    except ValueError as e:
        logger.error(f"[Screenshot] No vision model available: {e}")
        return f'{{"question":"Screenshot Question","answer":"No vision-capable API key configured. Please add GEMINI_API_KEY or OPENAI_API_KEY to your backend environment on Render."}}'

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
            response = await client.chat.completions.create(
                model=vision_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Extract the exact question/problem visible in this screenshot into \"question\", and answer it fully in \"answer\"."},
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
            logger.error(f"[Screenshot] OpenAI Vision error with {vision_model}: {e}")
            if vision_model not in ("gpt-4o", "gpt-4o-mini"):
                try:
                    logger.info("[Screenshot] Retrying vision with gpt-4o standard fallback...")
                    fb_response = await client.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": "Extract the exact question/problem visible in this screenshot into \"question\", and answer it fully in \"answer\"."},
                                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                                ],
                            },
                        ],
                        temperature=0.3,
                        response_format={"type": "json_object"},
                    )
                    fb_content = fb_response.choices[0].message.content or ""
                    if log_file:
                        log_response(log_file, response_text=fb_content)
                    return fb_content
                except Exception as fb_err:
                    logger.error(f"[Screenshot] OpenAI Vision fallback failed: {fb_err}")

            gemini_client = get_gemini_client()
            if not gemini_client:
                return f'{{"question":"Screenshot Question","answer":"Error analyzing screenshot via OpenAI: {str(e)}"}}'
            logger.info("[Screenshot] Falling back to Gemini vision...")
            provider = "gemini"
            vision_model = "gemini-2.5-flash"
            client = gemini_client

    # ── Gemini Vision ──────────────────────────────────────────────────────────
    if provider == "gemini":
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
                lambda: client.models.generate_content(
                    model=vision_model,
                    contents=[
                        types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                        "Extract the exact question/problem visible in this screenshot into \"question\", and answer it fully in \"answer\".",
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
            return f'{{"question":"Screenshot Question","answer":"Error analyzing screenshot via Gemini: {str(e)}"}}'

    return '{"question":"Screenshot Question","answer":"No vision provider matched."}'
