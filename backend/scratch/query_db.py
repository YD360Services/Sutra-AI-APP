import asyncio
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from app.db.database import SessionLocal
from app.db.models import Session, Resume, KnowledgeDocument

async def main():
    async with SessionLocal() as db:
        print("Checking PostgreSQL Database connection...")
        try:
            # 1. Check Sessions
            stmt = select(Session).options(selectinload(Session.question_answers)).order_by(Session.created_at.desc()).limit(5)
            res = await db.execute(stmt)
            sessions = res.scalars().all()
            print(f"\n--- Recent Sessions ({len(sessions)} records) ---")
            for s in sessions:
                print(f"ID: {s.id} | Name: {s.session_name} | Status: {s.status} | Duration: {s.duration_seconds}s | AI Usage: {s.ai_usage} | Summary: {s.summary is not None} (len: {len(s.summary) if s.summary else 0})")

            # 2. Check Resumes
            stmt = select(Resume)
            res = await db.execute(stmt)
            resumes = res.scalars().all()
            print(f"\n--- Resumes ({len(resumes)} records) ---")
            for r in resumes:
                print(f"ID: {r.id} | File: {r.file_name} | Active: {r.is_active}")

            # 3. Check Knowledge Documents
            stmt = select(KnowledgeDocument)
            res = await db.execute(stmt)
            docs = res.scalars().all()
            print(f"\n--- Knowledge Documents & Prompts ({len(docs)} records) ---")
            for d in docs:
                print(f"ID: {d.id} | Name: {d.document_name} | Type: {d.document_type}")

        except Exception as e:
            print(f"Database query failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
