from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
import uuid

from app.db.database import get_db
from app.db.repositories import JDRepository
from app.schemas.job_description import JDCreate, JDResponse

router = APIRouter()

@router.post("/job-descriptions", response_model=JDResponse)
async def create_job_description(
    payload: JDCreate,
    user_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db)
):
    repo = JDRepository(db)
    jd = await repo.create(
        user_id=user_id,
        company_name=payload.company_name,
        role_name=payload.role_name,
        description=payload.description
    )
    return jd

@router.get("/job-descriptions", response_model=List[JDResponse])
async def list_job_descriptions(
    user_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db)
):
    repo = JDRepository(db)
    jds = await repo.list_by_user(user_id=user_id)
    return jds

@router.patch("/job-descriptions/{jd_id}/activate", response_model=JDResponse)
async def activate_job_description(
    jd_id: uuid.UUID,
    user_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db)
):
    repo = JDRepository(db)
    jd = await repo.activate(user_id=user_id, jd_id=jd_id)
    if not jd:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job description not found"
        )
    return jd

@router.delete("/job-descriptions/{jd_id}")
async def delete_job_description(
    jd_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    repo = JDRepository(db)
    deleted = await repo.delete(jd_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job description not found"
        )
    return {"status": "success", "message": "Job description deleted"}
