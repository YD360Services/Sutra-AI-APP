import asyncio
from sqlalchemy.future import select
from app.db.database import SessionLocal
from app.db.models import Resume

async def main():
    async with SessionLocal() as db:
        stmt = select(Resume)
        res = await db.execute(stmt)
        resumes = res.scalars().all()
        print("Resumes in DB:")
        for r in resumes:
            print(f"File: {r.file_name} | User ID: {r.user_id}")
            
if __name__ == "__main__":
    asyncio.run(main())
