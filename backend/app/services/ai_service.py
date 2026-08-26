from google import genai
from google.genai import types
from app.core.config import settings
import logging
import openai
import anthropic as anthropic_sdk
import json
from datetime import datetime
import os
from pathlib import Path

logger = logging.getLogger("copilotx.ai_service")

print("=" * 90)
print("LOADED UPDATED ai_service.py")
print("FILE:", Path(__file__).resolve())
print("=" * 90)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if PROJECT_ROOT == Path("/"):
    PROJECT_ROOT = Path("/app")
PROMPT_LOG_DIR = PROJECT_ROOT / "logs" / "prompt_debug"
PROMPT_LOG_DIR.mkdir(parents=True, exist_ok=True)
ENABLE_PROMPT_LOGGING = True

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
        "project_root": str(PROJECT_ROOT),
        "prompt_log_dir": str(PROMPT_LOG_DIR),
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

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print("=" * 90)
    print("PROMPT LOG WRITTEN")
    print("Provider:", provider)
    print("Model:", model)
    print("Path:", filename)
    print("=" * 90)
    logger.info(f"Prompt saved -> {filename}")
    return filename

def log_response(filename: Path, response_text: str = None, error: str = None):
    if not ENABLE_PROMPT_LOGGING or not filename or not filename.exists():
        return
    try:
        with open(filename, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if response_text is not None:
            payload["response_text"] = response_text
        if error is not None:
            payload["error"] = error
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        logger.info(f"Response logged to {filename}")
    except Exception as e:
        logger.error(f"Failed to log response to {filename}: {e}")

# Initialize the Gemini Client
genai_client = None
if settings.GEMINI_API_KEY:
    try:
        genai_client = genai.Client(api_key=settings.GEMINI_API_KEY)
        logger.info("Gemini API Client initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing Gemini client: {e}")

# Initialize the Groq Client
groq_client = None
if settings.GROQ_API_KEY:
    try:
        groq_client = openai.AsyncOpenAI(
            api_key=settings.GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1"
        )
        logger.info("Groq API Client initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing Groq client: {e}")

# Initialize the OpenAI Client
openai_client = None
if settings.OPENAI_API_KEY:
    try:
        openai_client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        logger.info("OpenAI API Client initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing OpenAI client: {e}")

# Initialize the Anthropic (Claude) Client
anthropic_client = None
if settings.ANTHROPIC_API_KEY:
    try:
        anthropic_client = anthropic_sdk.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        logger.info("Anthropic (Claude) API Client initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing Anthropic client: {e}")

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

    # Helper mapping for clean API model strings (Cost-Saving Aliased)
    def map_to_real_api_model(m: str) -> str:
        if not m:
            return ""
        ml = m.lower().replace(" ", "-") # normalize spaces to dashes

        # ── 1. Google Gemini Normalizer ──
        if "3.7" in ml or "gemini-3.7" in ml or "lite" in ml:
            return "gemini-3.5-flash-lite"
        if "3.1" in ml or "gemini-3.1" in ml or "pro" in ml:
            return "gemini-3.5-flash"
        if "gemini" in ml or "flash" in ml:
            return "gemini-3.5-flash-lite"

        # ── 2. Anthropic Claude Normalizer ──
        if "claude" in ml or "sonnet" in ml or "haiku" in ml:
            return "claude-3-5-haiku"

        # ── 3. Meta / Groq Normalizer ──
        if "llama" in ml or "groq" in ml or "scout" in ml or "20b" in ml:
            return "llama-3.1-8b-instant"

        # ── 4. OpenAI GPTOSS Normalizer ──
        if "o3" in ml or "gptoss" in ml:
            return "gpt-4o-mini"

        # ── 5. OpenAI GPT 5.6 / GPT 5.5 Normalizer ──
        if "gpt-5.6" in ml or "gpt-5.5" in ml or "5.6" in ml or "5.5" in ml or "gpt" in ml:
            return "gpt-5.5-mini"

        return m

    # 1. Respect model_lower if explicitly requested
    if model_lower:
        resolved = map_to_real_api_model(model_lower)
        if resolved:
            return resolved

    # 2. Fallbacks based on task types if model is not set
    if is_live_answer:
        if settings.OPENAI_API_KEY:
            return "gpt-5.5-mini"
        elif settings.GEMINI_API_KEY:
            return "gemini-3.5-flash"
        elif settings.GROQ_API_KEY:
            return "meta-llama/llama-4-scout-17b-16e-instruct"
        return ""
    elif is_screenshot:
        if settings.OPENAI_API_KEY:
            return "gpt-5.5"
        elif settings.GEMINI_API_KEY:
            return "gemini-3.5-flash"
        return "gpt-5.5"
    else:
        if settings.GROQ_API_KEY:
            return "meta-llama/llama-4-scout-17b-16e-instruct"
        elif settings.OPENAI_API_KEY:
            return "gpt-5.5-mini"
        elif settings.GEMINI_API_KEY:
            return "gemini-3.5-flash"
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
        or "meta-llama/" in model_lower              # Llama 4 Scout format
        or ("openai/" in model_lower and "gpt-oss" in model_lower)  # gpt-oss-20b on Groq
        or "qwen" in model_lower                      # Qwen models on Groq
    )
    is_claude = "claude" in model_lower

    log_file = None

    if is_groq:
        if not groq_client:
            if throw_on_error:
                raise ValueError("Groq API Key or Client not configured.")
            logger.warning("Groq API Client not configured. Using local fallback.")
            return fallback_answer(prompt)
        try:
            response_format = {"type": "json_object"} if response_json else None
            log_file = log_prompt(provider="groq", model=model, system_prompt=system_prompt, user_prompt=prompt, response_json=response_json, temperature=temperature, stream=False)
            response = await groq_client.chat.completions.create(
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
            logger.error(f"Groq API generation error: {e}. Falling back...")
            if log_file:
                log_response(log_file, error=str(e))
            if throw_on_error:
                raise e
            return fallback_answer(prompt)

    elif is_claude:
        # Anthropic (Claude) Flow — system prompt is a top-level param, NOT inside messages[]
        if not anthropic_client:
            if throw_on_error:
                raise ValueError("Anthropic API Key or Client not configured.")
            logger.warning("Anthropic Client not configured. Using local fallback.")
            return fallback_answer(prompt)
        try:
            log_file = log_prompt(provider="anthropic", model=model, system_prompt=system_prompt, user_prompt=prompt, response_json=response_json, temperature=temperature, stream=False)
            response = await anthropic_client.messages.create(
                model=model,
                max_tokens=4096,
                system=system_prompt,          # <-- Anthropic's unique API: system is top-level
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            )
            content = response.content[0].text or ""
            if log_file:
                log_response(log_file, response_text=content)
            return content
        except Exception as e:
            logger.error(f"Anthropic (Claude) API generation error: {e}. Falling back...")
            if log_file:
                log_response(log_file, error=str(e))
            if throw_on_error:
                raise e
            return fallback_answer(prompt)

    elif "gpt" in model_lower:
        # OpenAI Flow
        if not openai_client:
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
                
            response = await openai_client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            if log_file:
                log_response(log_file, response_text=content)
            return content
        except Exception as e:
            logger.error(f"OpenAI API generation error: {e}. Falling back...")
            if log_file:
                log_response(log_file, error=str(e))
            if throw_on_error:
                raise e
            return fallback_answer(prompt)
    else:
        # Gemini Flow
        gemini_model = model if model else settings.GEMINI_MODEL
        if not genai_client:
            if throw_on_error:
                raise ValueError("Gemini Client not configured (no key).")
            logger.warning("Gemini Client not configured. Using local fallback.")
            return fallback_answer(prompt)
        try:
            config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=temperature,
            )
            if response_json:
                config.response_mime_type = "application/json"

            log_file = log_prompt(provider="gemini", model=gemini_model, system_prompt=system_prompt, user_prompt=prompt, response_json=response_json, temperature=temperature, stream=False)

            response = await genai_client.aio.models.generate_content(
                model=gemini_model,
                contents=prompt,
                config=config
            )
            content = response.text or ""
            if log_file:
                log_response(log_file, response_text=content)
            return content
        except Exception as e:
            logger.error(f"Gemini API generation error: {e}. Attempting OpenAI failover...")
            if openai_client:
                try:
                    response_format = {"type": "json_object"} if response_json else None
                    openai_resp = await openai_client.chat.completions.create(
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
        or "meta-llama/" in model_lower              # Llama 4 Scout format
        or ("openai/" in model_lower and "gpt-oss" in model_lower)  # gpt-oss-20b on Groq
        or "qwen" in model_lower                      # Qwen models on Groq
    )
    is_claude = "claude" in model_lower

    if is_groq:
        if not groq_client:
            yield fallback_answer(prompt)
            return
        try:
            response_format = {"type": "json_object"} if response_json else None
            log_prompt(provider="groq", model=model, system_prompt=system_prompt, user_prompt=prompt, response_json=response_json, temperature=0.3, stream=True)
            response = await groq_client.chat.completions.create(
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
        except Exception as e:
            logger.error(f"Groq streaming error: {e}")
            yield fallback_answer(prompt)

    elif is_claude:
        # Anthropic (Claude) streaming flow
        if not anthropic_client:
            yield fallback_answer(prompt)
            return
        try:
            log_prompt(provider="anthropic", model=model, system_prompt=system_prompt, user_prompt=prompt, response_json=response_json, temperature=0.3, stream=True)
            async with anthropic_client.messages.stream(
                model=model,
                max_tokens=4096,
                system=system_prompt,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as e:
            logger.error(f"Anthropic (Claude) streaming error: {e}")
            yield fallback_answer(prompt)

    elif "gpt" in model_lower:
        if not openai_client:
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
                
            response = await openai_client.chat.completions.create(**kwargs)
            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            logger.error(f"OpenAI streaming error: {e}")
            yield fallback_answer(prompt)
    else:
        # Gemini Flow
        gemini_model = model if model else settings.GEMINI_MODEL
        if not genai_client:
            yield fallback_answer(prompt)
            return
        try:
            config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.3,
            )
            if response_json:
                config.response_mime_type = "application/json"
            log_prompt(provider="gemini", model=gemini_model, system_prompt=system_prompt, user_prompt=prompt, response_json=response_json, temperature=0.3, stream=True)

            response_stream = await genai_client.aio.models.generate_content_stream(
                model=gemini_model,
                contents=prompt,
                config=config
            )
            async for chunk in response_stream:
                if chunk.text:
                    yield chunk.text
        except Exception as e:
            logger.error(f"Gemini streaming error: {e}")
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


