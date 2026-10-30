import asyncio
import os
import sys

sys.path.insert(0, 'backend')
from app.core.config import settings
from openai import AsyncOpenAI

async def test_openai():
    api_key = settings.OPENAI_API_KEY
    print(f"OpenAI Key configured: {bool(api_key)}")
    if api_key:
        print(f"Key prefix: {api_key[:12]}... (length: {len(api_key)})")
    
    client = AsyncOpenAI(api_key=api_key)
    
    test_models = ["gpt-4o-mini", "gpt-4o", "gpt-5.6-luna", "o3-mini"]
    
    for m in test_models:
        try:
            print(f"\nTesting model: {m} ...")
            resp = await client.chat.completions.create(
                model=m,
                messages=[{"role": "user", "content": "Say hello in one word"}],
                max_tokens=10
            )
            print(f"-> SUCCESS: {resp.choices[0].message.content.strip()}")
        except Exception as e:
            print(f"-> FAILED: {type(e).__name__} - {e}")

if __name__ == "__main__":
    asyncio.run(test_openai())
