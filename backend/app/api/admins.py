from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, EmailStr
from typing import List

from app.db.database import get_db
from app.db.models import AdminEmail
import logging

router = APIRouter()
logger = logging.getLogger("copilotx.admins")

DEFAULT_ADMINS = ['kirankumar82054@gmail.com', 'omkarvenkat07@gmail.com']

class AdminEmailCreate(BaseModel):
    email: EmailStr

@router.get("/admins", response_model=List[str])
async def get_admins(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AdminEmail.email))
    admins = result.scalars().all()
    
    if not admins:
        logger.info("No admins found in DB, seeding default admins.")
        for email in DEFAULT_ADMINS:
            new_admin = AdminEmail(email=email)
            db.add(new_admin)
        await db.commit()
        return DEFAULT_ADMINS
        
    return list(admins)

@router.post("/admins", response_model=str)
async def add_admin(
    payload: AdminEmailCreate,
    db: AsyncSession = Depends(get_db)
):
    # Check if already exists
    result = await db.execute(select(AdminEmail).where(AdminEmail.email == payload.email))
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin already exists"
        )
        
    new_admin = AdminEmail(email=payload.email)
    db.add(new_admin)
    await db.commit()
    
    logger.info(f"Added new admin: {payload.email}")
    return payload.email

@router.delete("/admins/{email}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_admin(
    email: str,
    db: AsyncSession = Depends(get_db)
):
    # Check if exists
    result = await db.execute(select(AdminEmail).where(AdminEmail.email == email))
    admin = result.scalar_one_or_none()
    
    if not admin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin not found"
        )
        
    # Prevent deleting the last admin
    count_result = await db.execute(select(AdminEmail))
    count = len(count_result.scalars().all())
    
    if count <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove the last admin"
        )
        
    await db.delete(admin)
    await db.commit()
    
    logger.info(f"Removed admin: {email}")
    return None
