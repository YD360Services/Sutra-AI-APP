from google import genai
from google.genai import types
from app.core.config import settings
import logging
import openai
try:
    import anthropic as anthropic_sdk
except ImportError:
    anthropic_sdk = None
import json
from datetime import datetime
import os
from pathlib import Path

logger = logging.getLogger("roundmate.ai_service")

print("=" * 90)
print("LOADED UPDATED ai_service.py")
print("FILE:", Path(__file__).resolve())
print("=" * 90)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if PROJECT_ROOT == Path("/"):
    PROJECT_ROOT = Path("/app")
PROMPT_LOG_DIR = PROJECT_ROOT / "logs" / "prompt_debug"
PROMPT_LOG_DIR.mkdir(parents=True, exist_ok=True)
ENABLE_PROMPT_LOGGING = os.getenv("ENABLE_PROMPT_LOGGING", "false").lower() in ("true", "1")

def _async_write_log(filename: Path, payload: dict):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.debug(f"Async log write failed: {e}")

def log_prompt(provider: str,
               model: str,
               system_prompt: str,
               user_prompt: str,
               response_json: bool = False,
               temperature: float = 0.3,
               stream: bool = False) -> Path:
    if not ENABLE_PROMPT_LOGGING:
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_provider = provider.replace(":", "_").replace("/", "_")
    safe_model = (model or "unknown").replace(":", "_").replace("/", "_")
    filename = PROMPT_LOG_DIR / f"{timestamp}_{safe_provider}_{safe_model}.json"

    payload = {
        "timestamp": timestamp,
        "provider": provider,
        "model": model,
        "response_json": response_json,
        "temperature": temperature,
        "stream": stream,
        "system_prompt_chars": len(system_prompt or ""),
        "user_prompt_chars": len(user_prompt or ""),
        "total_chars": len(system_prompt or "") + len(user_prompt or ""),
        "system_prompt": system_prompt or "",
        "user_prompt": user_prompt or "",
        "messages": [
            {"role": "system", "content": system_prompt or ""},
            {"role": "user", "content": user_prompt or ""}
        ]
    }

    # Offload disk write to non-blocking daemon thread so hot path TTFT is not stalled by disk I/O
    import threading
    threading.Thread(target=_async_write_log, args=(filename, payload), daemon=True).start()
    return filename

def _async_write_response(filename: Path, response_text: str = None, error: str = None):
    try:
        if not filename.exists():
            return
        with open(filename, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if response_text is not None:
            payload["response_text"] = response_text
        if error is not None:
            payload["error"] = error
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.debug(f"Failed to log response to {filename}: {e}")

def log_response(filename: Path, response_text: str = None, error: str = None):
    if not ENABLE_PROMPT_LOGGING or not filename:
        return
    import threading
    threading.Thread(target=_async_write_response, args=(filename, response_text, error), daemon=True).start()

# Dynamic Client Initialization Helpers
genai_client = None
groq_client = None
openai_client = None
anthropic_client = None
xai_client = None
_gemini_disabled = False

def is_gemini_disabled() -> bool:
    global _gemini_disabled
    return _gemini_disabled

def disable_gemini():
    global _gemini_disabled
    _gemini_disabled = True

def get_gemini_client():
    global genai_client
    if is_gemini_disabled():
        return None
    if genai_client is None and settings.GEMINI_API_KEY:
        try:
            genai_client = genai.Client(api_key=settings.GEMINI_API_KEY)
            logger.info("Gemini API Client initialized successfully.")
        except Exception as e:
            logger.error(f"Error initializing Gemini client: {e}")
    return genai_client

def get_groq_client():
    global groq_client
    if groq_client is None and settings.GROQ_API_KEY:
        try:
            groq_client = openai.AsyncOpenAI(
                api_key=settings.GROQ_API_KEY,
                base_url="https://api.groq.com/openai/v1"
            )
            logger.info("Groq API Client initialized successfully.")
        except Exception as e:
            logger.error(f"Error initializing Groq client: {e}")
    return groq_client

def get_openai_client():
    global openai_client
    if openai_client is None and settings.OPENAI_API_KEY:
        try:
            openai_client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
            logger.info("OpenAI API Client initialized successfully.")
        except Exception as e:
            logger.error(f"Error initializing OpenAI client: {e}")
    return openai_client

def get_xai_client():
    global xai_client
    api_key = getattr(settings, "XAI_API_KEY", "") or os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY")
    if xai_client is None and api_key:
        try:
            xai_client = openai.AsyncOpenAI(
                api_key=api_key,
                base_url="https://api.x.ai/v1"
            )
            logger.info("xAI (Grok) API Client initialized successfully.")
        except Exception as e:
            logger.error(f"Error initializing xAI client: {e}")
    return xai_client

def get_anthropic_client():
    global anthropic_client
    if anthropic_client is None and settings.ANTHROPIC_API_KEY:
        try:
            anthropic_client = anthropic_sdk.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
            logger.info("Anthropic (Claude) API Client initialized successfully.")
        except Exception as e:
            logger.error(f"Error initializing Anthropic client: {e}")
    return anthropic_client

def fallback_answer(question: str) -> str:
    q = question.lower()
    if "redis" in q:
        return "Redis is an in-memory data store commonly used for caching, rate limiting, queues, and session storage. In a scalable system, I would use it with a cache-aside pattern: read from Redis first, fallback to the database on cache miss, then populate Redis with TTL. This reduces database load and improves latency, while TTL and invalidation policies control freshness."
    if "sql" in q:
        return "I would start by checking the execution plan, then optimize the query by filtering early, selecting only required columns, adding the right indexes, avoiding unnecessary joins, and validating statistics. I would measure before and after using latency, scanned rows, CPU, and IO rather than blindly adding indexes."
    if "design" in q or "system" in q:
        return "I would first clarify scale, availability, latency, and consistency requirements. Then I would design stateless API services behind a load balancer, use PostgreSQL for durable metadata, Redis for hot cache, and Kafka or RabbitMQ for async processing. For scaling, I would add horizontal service scaling, database partitioning or read replicas, cache TTLs, retries, dead-letter queues, and monitoring."
    return "I would answer this by first clarifying the requirement, then explaining the approach, tradeoffs, and production considerations. I would keep the solution simple initially, optimize the bottleneck based on metrics, and design for reliability, scalability, and maintainability."

def resolve_model_by_task(model: str = None, system_prompt: str = "") -> str:
    sys_lower = system_prompt.lower() if system_prompt else ""
    model_lower = model.lower() if model else ""

    # Task detection logic
    is_live_answer = "candidate" in sys_lower or "interview" in sys_lower or "grammar" in sys_lower
    is_screenshot = "screenshot" in model_lower or "screenshot" in sys_lower
    is_resume_parse = "resume" in sys_lower and "summar" in sys_lower
    is_mock = "mock" in model_lower or "mock" in sys_lower or "feedback" in sys_lower

    # Helper mapping for clean real API model strings
    def map_to_real_api_model(m: str) -> str:
        if not m:
            return ""
        ml = m.lower().replace(" ", "-")

        # ── 1. Google Gemini Normalizer ──
        # Gemini 4 Argon (Recommended #2) -> Google: gemini-3.8-flash
        if "argon" in ml or "4-argon" in ml:
            return "gemini-3.8-flash"
        # Gemini 3.8 Flash -> Google: gemini-3.5-flash-lite
        if "gemini-3.8" in ml or "3.8-flash" in ml or "3.8" in ml or "3.5" in ml or "flash-lite" in ml or "lite-tts" in ml:
            return "gemini-3.5-flash-lite"
        if "gemini" in ml:
            return "gemini-3.8-flash"

        # ── 2. Anthropic Claude Normalizer ──
        # Claude Fable 5.1 -> Anthropic: claude-sonnet-5-5
        if "fable" in ml or "fable-5.1" in ml or "fable-5" in ml:
            return "claude-sonnet-5-5"
        # claude-opus-5-5 -> Anthropic: claude-haiku-5-5
        if "opus" in ml or "opus-5" in ml or "opus-5-5" in ml:
            return "claude-haiku-5-5"
        if "sonnet" in ml or "3.7" in ml:
            return "claude-sonnet-5-5"
        if "claude" in ml or "haiku" in ml or "3.5" in ml:
            return "claude-haiku-5-5"

        # ── 3. Groq LPU Normalizer ──
        # GPT OSS (GPT OSS 20B) -> Groq LPU: openai/gpt-oss-120b
        if "gpt-oss" in ml or "oss-20b" in ml or "oss" in ml:
            return "openai/gpt-oss-120b"
        if "qwen" in ml:
            return "qwen/qwen3.8-27b"

        # ── 4. OpenAI 6-Series & Sol / Luna ──
        # GPT 6 Astra (Recommended #1) -> OpenAI: gpt-6.1-terra
        if any(x in ml for x in ["astra", "6-astra", "gpt-6-astra", "terra", "6.1-terra", "gpt-6.1-terra"]):
            return "gpt-6.1-terra"
        # 5. GPT-6.1 Sol (gpt-6.1-sol) -> OpenAI: gpt-6-luna
        if any(x in ml for x in ["sol", "6.1-sol", "gpt-6.1-sol"]):
            return "gpt-6-luna"
        # GPT-6 Luna -> OpenAI: gpt-6-luna
        if any(x in ml for x in ["luna", "6-luna", "gpt-6-luna", "5.6-luna"]):
            return "gpt-6-luna"
        if "4o-mini" in ml:
            return "gpt-4o-mini"
        if "4o" in ml:
            return "gpt-4o"
        if "mini" in ml or "gpt" in ml:
            return "gpt-6-luna"

        # ── 5. xAI Grok Normalizer ──
        if "grok-3-mini" in ml or "grok-mini" in ml or ("grok" in ml and "mini" in ml):
            return "grok-3-mini"
        if "grok" in ml:
            return "grok-3"

        # ── 6. Pass through all other model strings unchanged ──
        # This preserves exact model names like gpt-4.1-mini, gpt-4o, gpt-4-turbo, etc.
        return m

    # 1. Respect model_lower if explicitly requested
    if model_lower:
        resolved = map_to_real_api_model(model_lower)
        if resolved:
            return resolved

    # 2. Fallbacks based on task types if model is not set
    if is_live_answer:
        if settings.GEMINI_API_KEY and not is_gemini_disabled():
            return "gemini-3.8-flash-lite-tts"
        elif settings.GROQ_API_KEY:
            return settings.GROQ_MODEL or "openai/gpt-oss-120b"
        elif settings.OPENAI_API_KEY:
            return "gpt-4o-mini"
        elif settings.ANTHROPIC_API_KEY:
            return "claude-haiku-4-5-20251001"
        return ""
    elif is_screenshot:
        if settings.GEMINI_API_KEY and not is_gemini_disabled():
            return "gemini-3.6-flash"
        elif settings.OPENAI_API_KEY:
            return "gpt-4o-mini"
        return "gemini-3.6-flash"
    else:
        if settings.GEMINI_API_KEY and not is_gemini_disabled():
            return "gemini-3.8-flash-lite-tts"
        elif settings.GROQ_API_KEY:
            return settings.GROQ_MODEL or "openai/gpt-oss-120b"
        elif settings.OPENAI_API_KEY:
            return "gpt-4o-mini"
        return ""

async def call_llm(prompt: str, system_prompt: str, model: str = None, response_json: bool = False, throw_on_error: bool = False, temperature: float = 0.3) -> str:
    # Resolve the model based on task type and parameters
    model = resolve_model_by_task(model, system_prompt)
    if not model:
        if throw_on_error:
            raise ValueError("No LLM API keys configured.")
        logger.warning("No LLM keys configured. Using local fallback answers.")
        return fallback_answer(prompt)

    model_lower = model.lower()
    is_groq = (
        "llama" in model_lower or "mixtral" in model_lower
        or "gemma2" in model_lower or "groq" in model_lower
        or "qwen" in model_lower or "gpt-oss" in model_lower
        or "oss" in model_lower
        or "/" in model_lower
    )
    is_claude = "claude" in model_lower or "fable" in model_lower or "opus" in model_lower
    is_grok = "grok" in model_lower

    log_file = None

    if is_groq:
        client = get_groq_client()
        if client:
            try:
                response_format = {"type": "json_object"} if response_json else None
                log_file = log_prompt(provider="groq", model=model, system_prompt=system_prompt, user_prompt=prompt, response_json=response_json, temperature=temperature, stream=False)
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=temperature,
                    response_format=response_format
                )
                content = response.choices[0].message.content or ""
                if log_file:
                    log_response(log_file, response_text=content)
                return content
            except Exception as e:
                logger.error(f"Groq API generation error: {e}. Attempting failover...")
                if log_file:
                    log_response(log_file, error=str(e))

        # Failover to OpenAI if Groq is unconfigured or failed
        openai_fallback = get_openai_client()
        if openai_fallback:
            try:
                fallback_m = "gpt-4o-mini"
                response_format = {"type": "json_object"} if response_json else None
                openai_resp = await openai_fallback.chat.completions.create(
                    model=fallback_m,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=temperature,
                    response_format=response_format
                )
                return openai_resp.choices[0].message.content or ""
            except Exception as oe:
                logger.error(f"Groq failover error: {oe}")

        if throw_on_error:
            raise ValueError("Groq API not available and failover exhausted.")
        return fallback_answer(prompt)

    elif is_claude:
        # Anthropic (Claude) Flow — system prompt is a top-level param, NOT inside messages[]
        client = get_anthropic_client()
        if client:
            try:
                log_file = log_prompt(provider="anthropic", model=model, system_prompt=system_prompt, user_prompt=prompt, response_json=response_json, temperature=temperature, stream=False)
                response = await client.messages.create(
                    model=model,
                    max_tokens=4096,
                    system=system_prompt,
                    messages=[{"role": "user", "content": prompt}],
                )
                content = response.content[0].text or ""
                if log_file:
                    log_response(log_file, response_text=content)
                return content
            except Exception as e:
                logger.error(f"Anthropic (Claude) API generation error: {e}. Failing over...")
                if log_file:
                    log_response(log_file, error=str(e))

        # Failover to OpenAI if Anthropic is unconfigured or failed
        openai_fallback_client = get_openai_client()
        if openai_fallback_client:
            try:
                fallback_m = "gpt-4o" if ("sonnet" in model_lower or "fable" in model_lower) else "gpt-4o-mini"
                response_format = {"type": "json_object"} if response_json else None
                openai_resp = await openai_fallback_client.chat.completions.create(
                    model=fallback_m,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=temperature,
                    response_format=response_format
                )
                return openai_resp.choices[0].message.content or ""
            except Exception as oe:
                logger.error(f"Claude failover error: {oe}")

        if throw_on_error:
            raise ValueError("Anthropic API not available and failover exhausted.")
        return fallback_answer(prompt)

    elif is_grok:
        # xAI (Grok) Flow
        client = get_xai_client()
        if client:
            try:
                response_format = {"type": "json_object"} if response_json else None
                log_file = log_prompt(provider="xai", model=model, system_prompt=system_prompt, user_prompt=prompt, response_json=response_json, temperature=temperature, stream=False)
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=temperature,
                    response_format=response_format
                )
                content = resp.choices[0].message.content or ""
                if log_file:
                    log_response(log_file, response_text=content)
                return content
            except Exception as ge:
                logger.error(f"xAI (Grok) API generation error: {ge}. Failing over...")

        # Failover to OpenAI if xAI unconfigured or failed
        openai_fallback = get_openai_client()
        if openai_fallback:
            try:
                fallback_m = "gpt-4o" if "mini" not in model_lower else "gpt-4o-mini"
                response_format = {"type": "json_object"} if response_json else None
                openai_resp = await openai_fallback.chat.completions.create(
                    model=fallback_m,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=temperature,
                    response_format=response_format
                )
                return openai_resp.choices[0].message.content or ""
            except Exception as oe:
                logger.error(f"Grok failover error: {oe}")

        if throw_on_error:
            raise ValueError("xAI (Grok) API not available and failover exhausted.")
        return fallback_answer(prompt)

    elif "gpt" in model_lower or "o3" in model_lower or "terra" in model_lower or "sol" in model_lower or "luna" in model_lower:
        # OpenAI Flow — Target model: gpt-6.1-terra / gpt-6.1-sol / gpt-6-luna
        client = get_openai_client()
        if not client:
            if throw_on_error:
                raise ValueError("OpenAI API Key or Client not configured.")
            logger.warning("OpenAI API Client not configured. Using local fallback.")
            return fallback_answer(prompt)
        try:
            response_format = {"type": "json_object"} if response_json else None
            log_file = log_prompt(provider="openai", model=model, system_prompt=system_prompt, user_prompt=prompt, response_json=response_json, temperature=temperature, stream=False)
            
            kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ]
            }
            if response_format:
                kwargs["response_format"] = response_format
            if not model.lower().startswith("o"):
                kwargs["temperature"] = temperature
                
            response = await client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            if log_file:
                log_response(log_file, response_text=content)
            return content
        except Exception as e:
            logger.error(f"OpenAI API generation error for {model}: {e}. Trying fallback...")
            # If backend model fails on standard public endpoint, fallback to gpt-4o / gpt-4o-mini
            fallback_m = "gpt-4o-mini" if ("luna" in model_lower or "mini" in model_lower) else "gpt-4o"
            if model != fallback_m:
                try:
                    logger.info(f"Failing over to standard {fallback_m}...")
                    fb_kwargs = {
                        "model": fallback_m,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": temperature
                    }
                    if response_format:
                        fb_kwargs["response_format"] = response_format
                    fb_resp = await client.chat.completions.create(**fb_kwargs)
                    fb_content = fb_resp.choices[0].message.content or ""
                    if log_file:
                        log_response(log_file, response_text=fb_content)
                    return fb_content
                except Exception as fb_err:
                    logger.error(f"OpenAI fallback error: {fb_err}")
            if log_file:
                log_response(log_file, error=str(e))
            if throw_on_error:
                raise e
            return fallback_answer(prompt)
    else:
        # Gemini Flow
        gemini_model = model if model else settings.GEMINI_MODEL
        client = get_gemini_client()
        if not client:
            if throw_on_error:
                raise ValueError("Gemini Client not configured (no key).")
            logger.warning("Gemini Client not configured. Using local fallback.")
            return fallback_answer(prompt)
        try:
            config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=temperature,
                thinking_config=types.ThinkingConfig(thinking_budget=0),  # Disables 5-7s thinking delay
            )
            if response_json:
                config.response_mime_type = "application/json"

            log_file = log_prompt(provider="gemini", model=gemini_model, system_prompt=system_prompt, user_prompt=prompt, response_json=response_json, temperature=temperature, stream=False)

            response = await client.aio.models.generate_content(
                model=gemini_model,
                contents=prompt,
                config=config
            )
            content = response.text or ""
            if log_file:
                log_response(log_file, response_text=content)
            return content
        except Exception as e:
            err_msg = str(e)
            if any(k in err_msg for k in ["403", "PERMISSION_DENIED", "leaked", "API_KEY_INVALID"]):
                disable_gemini()
            logger.error(f"Gemini API generation error: {e}. Attempting failover...")
            
            # 1. Fast Groq failover
            groq_fallback_client = get_groq_client()
            if groq_fallback_client:
                try:
                    response_format = {"type": "json_object"} if response_json else None
                    groq_resp = await groq_fallback_client.chat.completions.create(
                        model=settings.GROQ_MODEL or "openai/gpt-oss-120b",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=temperature,
                        response_format=response_format
                    )
                    content = groq_resp.choices[0].message.content or ""
                    if log_file:
                        log_response(log_file, response_text=content)
                    return content
                except Exception as ge:
                    logger.error(f"Groq failover error: {ge}")

            # 2. OpenAI failover
            openai_fallback_client = get_openai_client()
            if openai_fallback_client:
                try:
                    response_format = {"type": "json_object"} if response_json else None
                    openai_resp = await openai_fallback_client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=temperature,
                        response_format=response_format
                    )
                    content = openai_resp.choices[0].message.content or ""
                    if log_file:
                        log_response(log_file, response_text=content)
                    return content
                except Exception as oe:
                    logger.error(f"OpenAI failover error: {oe}")
            if log_file:
                log_response(log_file, error=str(e))
            if throw_on_error:
                raise e
            return fallback_answer(prompt)

async def call_gemini(prompt: str, system_prompt: str, response_json: bool = False, model: str = None) -> str:
    return await call_llm(prompt=prompt, system_prompt=system_prompt, model=model, response_json=response_json)

async def stream_llm(prompt: str, system_prompt: str, model: str = None, response_json: bool = False):
    # Resolve the model based on task type and parameters
    model = resolve_model_by_task(model, system_prompt)
    if not model:
        yield fallback_answer(prompt)
        return

    model_lower = model.lower()
    is_groq = (
        "llama" in model_lower or "mixtral" in model_lower
        or "gemma2" in model_lower or "groq" in model_lower
        or "qwen" in model_lower or "gpt-oss" in model_lower
        or "oss" in model_lower
        or "/" in model_lower
    )
    is_claude = "claude" in model_lower or "fable" in model_lower or "opus" in model_lower
    is_grok = "grok" in model_lower

    if is_groq:
        client = get_groq_client()
        if client:
            try:
                response_format = {"type": "json_object"} if response_json else None
                log_prompt(provider="groq", model=model, system_prompt=system_prompt, user_prompt=prompt, response_json=response_json, temperature=0.3, stream=True)
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    response_format=response_format,
                    stream=True
                )
                async for chunk in response:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
                return
            except Exception as e:
                logger.error(f"Groq streaming error: {e}. Failing over to OpenAI...")

        # Failover to OpenAI for streaming if Groq is unconfigured or failed
        openai_fallback = get_openai_client()
        if openai_fallback:
            try:
                fallback_m = "gpt-4o-mini"
                response = await openai_fallback.chat.completions.create(
                    model=fallback_m,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    stream=True
                )
                async for chunk in response:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
                return
            except Exception as oe:
                logger.error(f"Groq streaming failover error: {oe}")

        yield fallback_answer(prompt)
        return

    elif is_claude:
        # Anthropic (Claude) streaming flow
        client = get_anthropic_client()
        if client:
            try:
                log_prompt(provider="anthropic", model=model, system_prompt=system_prompt, user_prompt=prompt, response_json=response_json, temperature=0.3, stream=True)
                async with client.messages.stream(
                    model=model,
                    max_tokens=4096,
                    system=system_prompt,
                    messages=[{"role": "user", "content": prompt}],
                ) as stream:
                    async for text in stream.text_stream:
                        yield text
                return
            except Exception as e:
                logger.error(f"Anthropic (Claude) streaming error: {e}. Failing over...")

        # Failover to OpenAI for streaming if Anthropic is unconfigured or failed
        openai_fallback = get_openai_client()
        if openai_fallback:
            try:
                fallback_m = "gpt-4o" if ("sonnet" in model_lower or "fable" in model_lower) else "gpt-4o-mini"
                response = await openai_fallback.chat.completions.create(
                    model=fallback_m,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    stream=True
                )
                async for chunk in response:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
                return
            except Exception as oe:
                logger.error(f"Claude streaming failover error: {oe}")

        yield fallback_answer(prompt)

    elif is_grok:
        # xAI (Grok) Flow
        client = get_xai_client()
        if client:
            try:
                response_format = {"type": "json_object"} if response_json else None
                log_file = log_prompt(provider="xai", model=model, system_prompt=system_prompt, user_prompt=prompt, response_json=response_json, temperature=temperature, stream=False)
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=temperature,
                    response_format=response_format
                )
                content = resp.choices[0].message.content or ""
                if log_file:
                    log_response(log_file, response_text=content)
                yield content
                return
            except Exception as ge:
                logger.error(f"xAI (Grok) API generation error: {ge}. Failing over...")

        # Failover to OpenAI if xAI unconfigured or failed
        openai_fallback = get_openai_client()
        if openai_fallback:
            try:
                fallback_m = "gpt-4o" if "mini" not in model_lower else "gpt-4o-mini"
                response_format = {"type": "json_object"} if response_json else None
                openai_resp = await openai_fallback.chat.completions.create(
                    model=fallback_m,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    response_format=response_format
                )
                yield openai_resp.choices[0].message.content or ""
                return
            except Exception as oe:
                logger.error(f"Grok failover error: {oe}")

        yield fallback_answer(prompt)
        return

    elif "gpt" in model_lower or "o3" in model_lower or "terra" in model_lower or "sol" in model_lower or "luna" in model_lower:
        client = get_openai_client()
        if not client:
            yield fallback_answer(prompt)
            return
        try:
            response_format = {"type": "json_object"} if response_json else None
            log_prompt(provider="openai", model=model, system_prompt=system_prompt, user_prompt=prompt, response_json=response_json, temperature=0.3, stream=True)
            
            kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                "stream": True
            }
            if response_format:
                kwargs["response_format"] = response_format
            if not model.lower().startswith("o"):
                kwargs["temperature"] = 0.3
                
            response = await client.chat.completions.create(**kwargs)
            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            logger.error(f"OpenAI streaming error for model {model}: {e}")
            if model != "gpt-4o-mini":
                try:
                    logger.info("Failing over to gpt-4o-mini...")
                    response = await client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.3,
                        stream=True
                    )
                    async for chunk in response:
                        if chunk.choices and chunk.choices[0].delta.content:
                            yield chunk.choices[0].delta.content
                    return
                except Exception as fallback_e:
                    logger.error(f"OpenAI fallback error: {fallback_e}")
            yield fallback_answer(prompt)
    else:
        # Gemini Flow with seamless Groq/OpenAI streaming failover
        gemini_model = model if model else settings.GEMINI_MODEL
        client = get_gemini_client()
        if not client or is_gemini_disabled():
            # Direct low-latency streaming failover when Gemini is unavailable or disabled
            groq_fallback = get_groq_client()
            if groq_fallback:
                try:
                    logger.info("Streaming via Groq (Gemini unavailable/disabled)...")
                    response = await groq_fallback.chat.completions.create(
                        model=settings.GROQ_MODEL or "openai/gpt-oss-120b",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.3,
                        stream=True
                    )
                    async for chunk in response:
                        if chunk.choices and chunk.choices[0].delta.content:
                            yield chunk.choices[0].delta.content
                    return
                except Exception as ge:
                    logger.error(f"Groq direct streaming failover error: {ge}")

            openai_fallback = get_openai_client()
            if openai_fallback:
                try:
                    logger.info("Streaming via OpenAI (Gemini unavailable/disabled)...")
                    response = await openai_fallback.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.3,
                        stream=True
                    )
                    async for chunk in response:
                        if chunk.choices and chunk.choices[0].delta.content:
                            yield chunk.choices[0].delta.content
                    return
                except Exception as oe:
                    logger.error(f"OpenAI direct streaming failover error: {oe}")

            yield fallback_answer(prompt)
            return

        try:
            config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.3,
                thinking_config=types.ThinkingConfig(thinking_budget=0),  # Disables 5-7s thinking delay
            )
            if response_json:
                config.response_mime_type = "application/json"
            log_prompt(provider="gemini", model=gemini_model, system_prompt=system_prompt, user_prompt=prompt, response_json=response_json, temperature=0.3, stream=True)

            response_stream = await client.aio.models.generate_content_stream(
                model=gemini_model,
                contents=prompt,
                config=config
            )
            async for chunk in response_stream:
                if chunk.text:
                    yield chunk.text
        except Exception as e:
            err_msg = str(e)
            if any(k in err_msg for k in ["403", "PERMISSION_DENIED", "leaked", "API_KEY_INVALID"]):
                disable_gemini()
            logger.error(f"Gemini streaming error: {e}. Attempting failover to Groq/OpenAI...")

            # 1. Fast Groq streaming failover
            groq_fallback = get_groq_client()
            if groq_fallback:
                try:
                    logger.info("Failing over to Groq for streaming...")
                    response = await groq_fallback.chat.completions.create(
                        model=settings.GROQ_MODEL or "openai/gpt-oss-120b",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.3,
                        stream=True
                    )
                    async for chunk in response:
                        if chunk.choices and chunk.choices[0].delta.content:
                            yield chunk.choices[0].delta.content
                    return
                except Exception as ge:
                    logger.error(f"Groq streaming failover error: {ge}")

            # 2. OpenAI streaming failover
            openai_fallback = get_openai_client()
            if openai_fallback:
                try:
                    logger.info("Failing over to OpenAI for streaming...")
                    response = await openai_fallback.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.3,
                        stream=True
                    )
                    async for chunk in response:
                        if chunk.choices and chunk.choices[0].delta.content:
                            yield chunk.choices[0].delta.content
                    return
                except Exception as oe:
                    logger.error(f"OpenAI streaming failover error: {oe}")

            yield fallback_answer(prompt)

async def generate_resume_summaries(parsed_content: str) -> dict:
    import json
    prompt = f"""
Analyze the following parsed resume content and generate 5 distinct sections in JSON format:
1. "introduction": A comprehensive and natural speakable introduction (about 2-3 paragraphs) as if the candidate is answering the question: "Tell me about yourself" or "Introduce yourself" during an interview. Keep it engaging, professional, and detailed.
2. "professional_summary": A professional high-level summary of the candidate's core expertise, roles, and achievements.
3. "career_journey": A detailed walk-through of the candidate's career progression, highlighting their growth, transitions, and key experiences.
4. "strengths": A summary of their top technical and soft skills and match strengths.
5. "project_summary": A detailed summary of key projects, technologies used, and outcomes achieved.

CRITICAL REQUIREMENT: You MUST write ALL 5 sections (including introduction, professional_summary, career_journey, strengths, and project_summary) in the FIRST-PERSON perspective using 'I', 'my', 'me', 'myself' as if written/spoken directly by the candidate. NEVER use the candidate's name or third-person pronouns ('he', 'she', 'his', 'her', 'him', 'them') to describe the candidate.

You must respond with ONLY a valid raw JSON object matching this schema. Do not include markdown wrappers (like ```json), labels, or headers in your response.

Schema:
{{
  "introduction": "...",
  "professional_summary": "...",
  "career_journey": "...",
  "strengths": "...",
  "project_summary": "..."
}}

Resume Content:
{parsed_content}
"""
    system_prompt = (
        "You are an expert recruiter and candidate profile writer. "
        "You write high-quality, professional, and extremely natural candidate summaries and speakable introductions. "
        "Write ALL sections (introduction, professional_summary, career_journey, strengths, and project_summary) "
        "exclusively in the FIRST-PERSON perspective ('I', 'my', 'me', 'myself') as if written or spoken directly by the candidate. "
        "Do NOT use robotic openings or AI conversational fillers like 'Certainly', 'Sure', 'Absolutely', or 'My name is [Your Name]'. "
        "Start directly and naturally (e.g. 'I am a software engineer with...'). "
        "Ensure it sounds conversational, professional, and human."
    )
    
    try:
        raw_resp = await call_llm(
            prompt=prompt,
            system_prompt=system_prompt,
            model="gpt-4.1-mini",
            throw_on_error=True,
            temperature=0.3
        )
        cleaned = raw_resp.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        data = json.loads(cleaned)
        return {
            "introduction": data.get("introduction", ""),
            "professional_summary": data.get("professional_summary", ""),
            "career_journey": data.get("career_journey", ""),
            "strengths": data.get("strengths", ""),
            "project_summary": data.get("project_summary", "")
        }
    except Exception as e:
        logger.error(f"Error generating resume summaries: {e}")
        return {
            "introduction": "",
            "professional_summary": "",
            "career_journey": "",
            "strengths": "",
            "project_summary": ""
        }

async def extract_latest_question(text: str, model: str = None) -> str:
    import re
    text_clean = text.strip()
    if not text_clean:
        return ""
        
    # If the text is short (< 15 words), it is likely already just the question.
    if len(text_clean.split()) < 15:
        return text_clean
        
    system_prompt = (
        "You are an expert interviewer. Your task is to identify and extract only the latest actual question "
        "being asked by the interviewer from the provided transcript. Ignore irrelevant talk, noise, "
        "or filler words. Return ONLY the clean, clear question text itself. Do not include any preambles, "
        "explanations, or markdown formatting."
    )
    try:
        # Use a fast model for this extraction task
        model_to_use = model if model else "gpt-4o-mini"
        extracted = await call_llm(
            prompt=text_clean,
            system_prompt=system_prompt,
            model=model_to_use,
            throw_on_error=True,
            temperature=0.0
        )
        extracted = extracted.strip()
        if extracted:
            return extracted
    except Exception as e:
        logger.warning(f"Failed to extract question using LLM, using fallback heuristic: {e}")
        
    # Fallback heuristic: take the last 2 sentences
    sentences = re.split(r'(?<=[.?!])\s+', text_clean)
    if len(sentences) <= 1:
        return text_clean
    return " ".join(sentences[-2:])


