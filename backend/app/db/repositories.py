from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update, delete
from typing import List, Optional
import uuid
from datetime import datetime

from app.db.models import User, Session, TranscriptBlock, QuestionAnswer, Resume, KnowledgeDocument, JobDescription

class BaseRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

class UserRepository(BaseRepository):
    async def get_by_id(self, user_id: uuid.UUID) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.id == user_id))
        return result.scalars().first()

    async def get_by_email(self, email: str) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalars().first()

    async def create(self, email: str, password_hash: str, name: Optional[str] = None, id: Optional[uuid.UUID] = None) -> User:
        user = User(id=id, email=email, password_hash=password_hash, name=name)
        self.db.add(user)
        await self.db.flush()
        return user


class SessionRepository(BaseRepository):
    async def get_by_id(self, session_id: uuid.UUID) -> Optional[Session]:
        from sqlalchemy.orm import selectinload
        result = await self.db.execute(
            select(Session).where(Session.id == session_id).options(selectinload(Session.question_answers))
        )
        return result.scalars().first()

    async def list_by_user(self, user_id: Optional[uuid.UUID], limit: int = 500) -> List[Session]:
        from sqlalchemy.orm import selectinload
        from sqlalchemy import or_
        if user_id:
            stmt = select(Session).where(or_(Session.user_id == user_id, Session.user_id.is_(None))).options(selectinload(Session.question_answers)).order_by(Session.created_at.desc()).limit(limit)
        else:
            stmt = select(Session).options(selectinload(Session.question_answers)).order_by(Session.created_at.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())


    async def create(self, session_name: str, company_name: str, role_name: str, 
                     language: str, audio_source: str, user_id: Optional[uuid.UUID] = None,
                     job_description_id: Optional[uuid.UUID] = None) -> Session:
        session = Session(
            user_id=user_id,
            session_name=session_name,
            company_name=company_name,
            role_name=role_name,
            language=language,
            audio_source=audio_source,
            job_description_id=job_description_id,
            status="active"
        )
        self.db.add(session)
        await self.db.flush()
        return session

    async def update(self, session_id: uuid.UUID, **kwargs) -> Optional[Session]:
        session = await self.get_by_id(session_id)
        if session:
            for k, v in kwargs.items():
                setattr(session, k, v)
            if "status" in kwargs and kwargs["status"] in ["ended", "completed"] and not session.ended_at:
                session.ended_at = datetime.utcnow()
                if "duration_seconds" not in kwargs or kwargs["duration_seconds"] is None or kwargs["duration_seconds"] <= 0:
                    session.duration_seconds = int((session.ended_at - session.started_at).total_seconds())
            self.db.add(session)
            await self.db.flush()
        return session

    async def delete(self, session_id: uuid.UUID) -> bool:
        session = await self.get_by_id(session_id)
        if session:
            await self.db.delete(session)
            await self.db.flush()
            return True
        return False


class TranscriptRepository(BaseRepository):
    async def create(self, session_id: uuid.UUID, speaker: str, content: str, source: str) -> TranscriptBlock:
        block = TranscriptBlock(session_id=session_id, speaker=speaker, content=content, source=source)
        self.db.add(block)
        await self.db.flush()
        return block

    async def list_by_session(self, session_id: uuid.UUID) -> List[TranscriptBlock]:
        stmt = select(TranscriptBlock).where(TranscriptBlock.session_id == session_id).order_by(TranscriptBlock.created_at.asc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())


class QARepository(BaseRepository):
    async def create(self, session_id: uuid.UUID, question: str, answer: str, source_type: str) -> QuestionAnswer:
        qa = QuestionAnswer(session_id=session_id, question=question, answer=answer, source_type=source_type)
        self.db.add(qa)
        await self.db.flush()
        return qa

    async def list_by_session(self, session_id: uuid.UUID) -> List[QuestionAnswer]:
        stmt = select(QuestionAnswer).where(QuestionAnswer.session_id == session_id).order_by(QuestionAnswer.created_at.asc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())


class ResumeRepository(BaseRepository):
    async def create(self, user_id: Optional[uuid.UUID], file_name: str, parsed_content: str) -> Resume:
        # deactivate previous active resumes first
        if user_id:
            await self.db.execute(
                update(Resume).where(Resume.user_id == user_id).values(is_active=False)
            )
        
        # Generate resume summaries using LLM
        from app.services.ai_service import generate_resume_summaries
        summaries = await generate_resume_summaries(parsed_content)

        resume = Resume(
            user_id=user_id,
            file_name=file_name,
            parsed_content=parsed_content,
            is_active=True,
            introduction=summaries.get("introduction"),
            professional_summary=summaries.get("professional_summary"),
            career_journey=summaries.get("career_journey"),
            strengths=summaries.get("strengths"),
            project_summary=summaries.get("project_summary")
        )
        self.db.add(resume)
        await self.db.flush()
        return resume

    async def get_active(self, user_id: Optional[uuid.UUID]) -> Optional[Resume]:
        if not user_id:
            return None
        stmt = select(Resume).where(Resume.user_id == user_id, Resume.is_active == True)
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def list_by_user(self, user_id: Optional[uuid.UUID]) -> List[Resume]:
        stmt = select(Resume).where(Resume.user_id == user_id).order_by(Resume.uploaded_at.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def activate(self, user_id: Optional[uuid.UUID], resume_id: uuid.UUID) -> Optional[Resume]:
        if user_id:
            await self.db.execute(
                update(Resume).where(Resume.user_id == user_id).values(is_active=False)
            )
        resume = await self.db.get(Resume, resume_id)
        if resume and (not user_id or resume.user_id == user_id):
            resume.is_active = True
            self.db.add(resume)
            await self.db.flush()
            return resume
        return None

    async def delete(self, resume_id: uuid.UUID) -> bool:
        resume = await self.db.get(Resume, resume_id)
        if resume:
            await self.db.delete(resume)
            await self.db.flush()
            return True
        return False


class KnowledgeRepository(BaseRepository):
    async def create(self, user_id: Optional[uuid.UUID], name: str, doc_type: str, content: str) -> KnowledgeDocument:
        doc = KnowledgeDocument(user_id=user_id, document_name=name, document_type=doc_type, content=content)
        self.db.add(doc)
        await self.db.flush()
        return doc

    async def list_by_user(self, user_id: Optional[uuid.UUID]) -> List[KnowledgeDocument]:
        from sqlalchemy import or_
        stmt = select(KnowledgeDocument).where(
            or_(
                KnowledgeDocument.user_id == user_id,
                KnowledgeDocument.user_id == None  # include global docs with no user
            )
        ).order_by(KnowledgeDocument.uploaded_at.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def delete(self, document_id: uuid.UUID) -> bool:
        doc = await self.db.get(KnowledgeDocument, document_id)
        if doc:
            await self.db.delete(doc)
            await self.db.flush()
            return True
        return False


class JDRepository(BaseRepository):
    async def create(self, user_id: Optional[uuid.UUID], company_name: str, role_name: str, description: str) -> JobDescription:
        if user_id:
            await self.db.execute(
                update(JobDescription).where(JobDescription.user_id == user_id).values(is_active=False)
            )
        jd = JobDescription(user_id=user_id, company_name=company_name, role_name=role_name, description=description, is_active=True)
        self.db.add(jd)
        await self.db.flush()
        return jd

    async def get_active(self, user_id: Optional[uuid.UUID]) -> Optional[JobDescription]:
        if not user_id:
            return None
        stmt = select(JobDescription).where(JobDescription.user_id == user_id, JobDescription.is_active == True)
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def list_by_user(self, user_id: Optional[uuid.UUID]) -> List[JobDescription]:
        stmt = select(JobDescription).where(JobDescription.user_id == user_id).order_by(JobDescription.created_at.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def activate(self, user_id: Optional[uuid.UUID], jd_id: uuid.UUID) -> Optional[JobDescription]:
        if user_id:
            await self.db.execute(
                update(JobDescription).where(JobDescription.user_id == user_id).values(is_active=False)
            )
        jd = await self.db.get(JobDescription, jd_id)
        if jd and (not user_id or jd.user_id == user_id):
            jd.is_active = True
            self.db.add(jd)
            await self.db.flush()
            return jd
        return None

    async def delete(self, jd_id: uuid.UUID) -> bool:
        jd = await self.db.get(JobDescription, jd_id)
        if jd:
            await self.db.delete(jd)
            await self.db.flush()
            return True
        return False
