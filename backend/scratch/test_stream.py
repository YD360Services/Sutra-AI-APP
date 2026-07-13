import asyncio
import httpx
import json

async def main():
    payload = {
        "session_id": None,
        "question": "what are packages in Python",
        "source_type": "transcript",
        "resume_content": None,
        "knowledge_content": None,
        "model": "llama-3.1-8b-instant"
    }
    
    url = "http://127.0.0.1:8000/api/answer/stream"
    print(f"Sending request to {url}...")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream("POST", url, json=payload) as response:
                print(f"Response status code: {response.status_code}")
                if response.status_code != 200:
                    body = await response.aread()
                    print(f"Error body: {body.decode()}")
                    return
                    
                async for chunk in response.aiter_text():
                    print(chunk, end="", flush=True)
                print("\nStream finished successfully.")
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
