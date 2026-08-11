import asyncio
import sys
import os
import json
import httpx
from sqlalchemy import select, func, text

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.database import engine, verify_and_initialize_db, SessionLocal
from app.db.models import (
    User, JobDescription, Session, TranscriptBlock,
    QuestionAnswer, Resume, KnowledgeDocument, MockInterview, AdminEmail
)

MODELS = [
    ("users", User),
    ("job_descriptions", JobDescription),
    ("sessions", Session),
    ("transcript_blocks", TranscriptBlock),
    ("question_answers", QuestionAnswer),
    ("resumes", Resume),
    ("knowledge_documents", KnowledgeDocument),
    ("mock_interviews", MockInterview),
    ("admin_emails", AdminEmail),
]

async def check_database():
    print("================ DATABASE & TABLES DIAGNOSTICS ================")
    print(f"Connecting using engine: {engine.url}")
    
    # 1. Initialize & Verify DB
    try:
        await verify_and_initialize_db()
        print("✅ verify_and_initialize_db() completed successfully.")
    except Exception as e:
        print(f"❌ verify_and_initialize_db() failed: {e}")
        return False, {}

    # 2. Check each table & schema query
    db_ok = True
    table_stats = {}
    async with SessionLocal() as db:
        for table_name, model in MODELS:
            try:
                result = await db.execute(select(func.count()).select_from(model))
                count = result.scalar()
                table_stats[table_name] = {"status": "OK", "count": count}
                print(f"  🟢 Table '{table_name}': OK ({count} records)")
            except Exception as e:
                db_ok = False
                table_stats[table_name] = {"status": "ERROR", "error": str(e)}
                print(f"  🔴 Table '{table_name}': ERROR ({e})")

        # 3. Test DB Read/Write transaction capability
        print("\n--- Testing Read/Write Transaction ---")
        try:
            test_email = "db_test_check@example.com"
            # Delete if leftover
            await db.execute(text("DELETE FROM users WHERE email = :email"), {"email": test_email})
            await db.commit()
            
            test_user = User(email=test_email, name="Test Connection", password_hash="dummy_hash")
            db.add(test_user)
            await db.commit()
            
            res = await db.execute(select(User).where(User.email == test_email))
            inserted = res.scalar_one_or_none()
            if inserted:
                print(f"  🟢 DB Write & Read Test: SUCCESS (Created User ID: {inserted.id})")
                await db.delete(inserted)
                await db.commit()
                print("  🟢 DB Cleanup Test: SUCCESS")
            else:
                print("  🔴 DB Write & Read Test: FAILED to retrieve inserted record")
                db_ok = False
        except Exception as e:
            print(f"  🔴 DB Write/Read Transaction Failed: {e}")
            db_ok = False

    return db_ok, table_stats

async def check_api_endpoints():
    print("\n================ API ENDPOINTS DIAGNOSTICS ================")
    base_url = "http://localhost:8000"
    endpoints = [
        ("/", "GET"),
        ("/health", "GET"),
        ("/docs", "GET"),
        ("/openapi.json", "GET"),
        ("/api/sessions", "GET"),
        ("/api/admins/emails", "GET"),
    ]
    
    api_stats = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        for path, method in endpoints:
            url = f"{base_url}{path}"
            try:
                resp = await client.request(method, url)
                api_stats[path] = {"status_code": resp.status_code, "ok": resp.is_success or resp.status_code in (200, 307, 401)}
                print(f"  {'🟢' if resp.status_code < 500 else '🔴'} Endpoint '{path}': Status {resp.status_code}")
            except Exception as e:
                api_stats[path] = {"status_code": None, "error": str(e)}
                print(f"  🔴 Endpoint '{path}': Connection Failed ({e})")
                
    return api_stats

async def main():
    db_ok, table_stats = await check_database()
    api_stats = await check_api_endpoints()
    
    print("\n================ FINAL REPORT ================")
    report = {
        "database_active": db_ok,
        "engine": str(engine.url),
        "total_tables": len(MODELS),
        "table_stats": table_stats,
        "api_stats": api_stats
    }
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
