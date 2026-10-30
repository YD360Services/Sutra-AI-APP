import asyncio
import httpx

models_to_test = [
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-6-luna",
    "gpt-5.6",
    "gptoss",
    "o3-mini",
    "gemini-3.8-flash-lite-tts",
]

async def test_model(client, model):
    url = "https://round-mate-ai.onrender.com/api/answer/stream"
    payload = {
        "session_id": None,
        "question": "What is 2 + 2? Answer in one word.",
        "source_type": "manual",
        "model": model
    }
    try:
        async with client.stream("POST", url, json=payload, timeout=20.0) as resp:
            chunks = []
            async for chunk in resp.aiter_text():
                chunks.append(chunk)
            full = "".join(chunks).strip()
            is_fallback = "clarify scale" in full or "Redis is an in-memory" or "execution plan" in full
            # check if it contains the exact fallback phrase
            is_canned = any(phrase in full for phrase in [
                "clarify scale, availability",
                "Redis is an in-memory data store",
                "checking the execution plan"
            ])
            status_str = "FALLBACK PREDEFINED" if is_canned else "SUCCESS (REAL AI)"
            print(f"[{status_str}] Model: {model} -> Status: {resp.status_code}")
            print(f"   Excerpt: {full[:120]}...\n")
    except Exception as e:
        print(f"[ERROR] Model: {model} -> {e}\n")

async def main():
    print("Testing OpenAI & Gemini models against https://round-mate-ai.onrender.com ...\n")
    async with httpx.AsyncClient() as client:
        for m in models_to_test:
            await test_model(client, m)

if __name__ == "__main__":
    asyncio.run(main())
