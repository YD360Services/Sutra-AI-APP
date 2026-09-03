from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, cast, String, or_
from pydantic import BaseModel, EmailStr
from typing import List, Optional
import uuid
from datetime import datetime, timedelta

from app.core.config import settings
from app.db.database import get_db
from app.db.models import AdminEmail
import logging

router = APIRouter()
logger = logging.getLogger("copilotx.admins")

class AdminEmailCreate(BaseModel):
    email: EmailStr

async def verify_admin_user(
    x_admin_email: Optional[str] = Header(None, alias="X-Admin-Email"),
    db: AsyncSession = Depends(get_db)
):
    """
    Security check: Validates if the requesting user's email is stored in the database admin_emails table.
    """
    if not x_admin_email:
        return None
        
    target_email = x_admin_email.lower().strip()
    result = await db.execute(select(AdminEmail).where(AdminEmail.email.ilike(target_email)))
    admin = result.scalar_one_or_none()
    
    if not admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized: User email is not registered in the admin database."
        )
    return target_email

@router.get("/admins", response_model=List[str])
async def get_admins(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AdminEmail.email))
    admins = result.scalars().all()
    
    if not admins:
        logger.info("No admins found in DB, seeding default admins from config.")
        initial_seeds = [
            e.strip().lower() 
            for e in getattr(settings, "INITIAL_ADMIN_EMAILS", "").split(",") 
            if e.strip()
        ]
        for email in initial_seeds:
            new_admin = AdminEmail(email=email)
            db.add(new_admin)
        await db.commit()
        return initial_seeds
        
    return list(admins)

@router.post("/admins", response_model=str)
async def add_admin(
    payload: AdminEmailCreate,
    db: AsyncSession = Depends(get_db)
):
    target_email = payload.email.lower().strip()
    # Check if already exists
    result = await db.execute(select(AdminEmail).where(AdminEmail.email.ilike(target_email)))
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin already exists"
        )
        
    new_admin = AdminEmail(email=target_email)
    db.add(new_admin)
    await db.commit()
    
    logger.info(f"Added new admin: {target_email}")
    return target_email

@router.delete("/admins/{email}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_admin(
    email: str,
    db: AsyncSession = Depends(get_db)
):
    target_email = email.lower().strip()
    # Check if exists
    result = await db.execute(select(AdminEmail).where(AdminEmail.email.ilike(target_email)))
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
    
    logger.info(f"Removed admin: {target_email}")
    return None

class UserPlanUpdate(BaseModel):
    plan: str

@router.get("/users")
async def get_users(
    plan: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    recent_days: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    from app.db.models import User, Session
    query = select(User)
    
    if plan and plan.lower() != 'all':
        if plan.lower() == 'paid':
            query = query.where(~User.plan.ilike('%free%'), ~User.plan.ilike('%admin%'))
        else:
            query = query.where(User.plan.ilike(f"%{plan}%"))
        
    if recent_days and recent_days > 0:
        cutoff = datetime.utcnow() - timedelta(days=recent_days)
        query = query.where(User.created_at >= cutoff)
        
    if start_date:
        try:
            sd = datetime.fromisoformat(start_date)
            query = query.where(User.created_at >= sd)
        except Exception:
            pass
            
    if end_date:
        try:
            ed = datetime.fromisoformat(end_date)
            if len(end_date) <= 10:
                ed = ed.replace(hour=23, minute=59, second=59)
            query = query.where(User.created_at <= ed)
        except Exception:
            pass

    query = query.order_by(User.created_at.desc())
    result = await db.execute(query)
    users = result.scalars().all()
    
    output = []
    for u in users:
        sess_result = await db.execute(select(Session).where(Session.user_id == u.id))
        user_sessions = sess_result.scalars().all()
        session_count = len(user_sessions)
        tokens_used = sum((s.duration_seconds or 0) * 15 for s in user_sessions) or (1400 if u.plan == "Pro" else (2500 if u.plan == "Enterprise" else 350))
        
        output.append({
            "id": str(u.id),
            "email": u.email,
            "name": u.name or u.email.split('@')[0].capitalize(),
            "plan": u.plan or "Free",
            "tokens_used": tokens_used,
            "session_count": session_count,
            "created_at": u.created_at.isoformat() if u.created_at else datetime.utcnow().isoformat()
        })
    return output

@router.patch("/users/{user_id}/plan")
async def update_user_plan(
    user_id: str,
    payload: UserPlanUpdate,
    db: AsyncSession = Depends(get_db)
):
    from app.db.models import User
    from sqlalchemy import or_
    try:
        user = None
        try:
            uid = uuid.UUID(user_id)
            result = await db.execute(select(User).where(User.id == uid))
            user = result.scalar_one_or_none()
        except Exception:
            pass

        if not user:
            # Fallback check by email or string comparison
            result = await db.execute(select(User).where(or_(User.email == user_id.lower().trim(), cast(User.id, String) == user_id)))
            user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        user.plan = payload.plan
        await db.commit()
        return {"success": True, "user_id": user_id, "plan": payload.plan}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error updating user plan: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

