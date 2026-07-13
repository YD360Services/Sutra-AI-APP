import asyncio
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from app.db.database import SessionLocal
from app.db.models import Session

async def main():
    async with SessionLocal() as db:
        stmt = select(Session).options(selectinload(Session.question_answers)).filter(Session.session_name.like("%dfghjk%"))
        res = await db.execute(stmt)
        sessions = res.scalars().all()
        print(f"Found {len(sessions)} sessions matching 'dfghjk':")
        for s in sessions:
            print(f"ID: {s.id} | Name: {s.session_name} | Status: {s.status} | Duration: {s.duration_seconds}s | AI Usage: {s.ai_usage} | Created At: {s.created_at}")
            
if __name__ == "__main__":
    asyncio.run(main())
