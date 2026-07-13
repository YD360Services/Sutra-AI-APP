import asyncio
import httpx

async def main():
    url = "http://127.0.0.1:8000/api/sessions/1d6ebd5f-806e-44e5-bed4-61fcf2bfad98"
    payload = {
        "status": "ended",
        "summary": "test summary",
        "duration_seconds": 19
    }
    async with httpx.AsyncClient() as client:
        res = await client.patch(url, json=payload)
        print("Status code:", res.status_code)
        print("Response body:", res.text)

if __name__ == "__main__":
    asyncio.run(main())
