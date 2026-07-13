import asyncio
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from app.db.database import SessionLocal
from app.db.models import Session, TranscriptBlock, QuestionAnswer

async def main():
    async with SessionLocal() as db:
        print("Checking transcripts and Q&As in DB...")
        try:
            # Check all transcript blocks
            stmt = select(TranscriptBlock).order_by(TranscriptBlock.created_at.desc()).limit(10)
            res = await db.execute(stmt)
            tblocks = res.scalars().all()
            print(f"\n--- Recent Transcript Blocks ({len(tblocks)} records) ---")
            for t in tblocks:
                print(f"SessionID: {t.session_id} | Speaker: {t.speaker} | Content: {t.content}")

            # Check all Q&As
            stmt = select(QuestionAnswer).order_by(QuestionAnswer.created_at.desc()).limit(10)
            res = await db.execute(stmt)
            qas = res.scalars().all()
            print(f"\n--- Recent Q&As ({len(qas)} records) ---")
            for q in qas:
                print(f"SessionID: {q.session_id} | Question: {q.question} | Answer: {q.answer[:50]}...")

        except Exception as e:
            print(f"Query failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
