import json
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import Notification

router = APIRouter(prefix="/notifications", tags=["Notifications"])

class NotificationCreate(BaseModel):
    id: str
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    type: str = "info"
    title: str
    message: Optional[str] = ""
    duration: int = 4500
    target_screen: Optional[str] = None
    target_tab: Optional[str] = None

class NotificationResponse(BaseModel):
    id: str
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    type: str
    title: str
    message: Optional[str] = ""
    read: bool
    duration: int
    target_screen: Optional[str] = None
    target_tab: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

@router.get("", response_model=List[NotificationResponse])
async def list_notifications(
    user_id: Optional[str] = Query(None),
    user_email: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Notification)
    if user_email:
        stmt = stmt.where(Notification.user_email == user_email.lower())
    elif user_id:
        stmt = stmt.where(Notification.user_id == user_id)
    else:
        return []

    stmt = stmt.order_by(Notification.created_at.desc()).limit(50)
    result = await db.execute(stmt)
    return result.scalars().all()

@router.post("", response_model=NotificationResponse)
async def create_or_upsert_notification(
    payload: NotificationCreate,
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Notification).where(Notification.id == payload.id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        existing.title = payload.title
        existing.message = payload.message
        existing.type = payload.type
        existing.duration = payload.duration
        existing.target_screen = payload.target_screen
        existing.target_tab = payload.target_tab
        if payload.user_email:
            existing.user_email = payload.user_email.lower()
        if payload.user_id:
            existing.user_id = payload.user_id
        await db.commit()
        await db.refresh(existing)
        return existing
    else:
        new_notif = Notification(
            id=payload.id,
            user_id=payload.user_id,
            user_email=payload.user_email.lower() if payload.user_email else None,
            type=payload.type,
            title=payload.title,
            message=payload.message or "",
            read=False,
            duration=payload.duration,
            target_screen=payload.target_screen,
            target_tab=payload.target_tab,
            created_at=datetime.utcnow()
        )
        db.add(new_notif)
        await db.commit()
        await db.refresh(new_notif)
        return new_notif

@router.patch("/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    db: AsyncSession = Depends(get_db)
):
    stmt = update(Notification).where(Notification.id == notification_id).values(read=True)
    await db.execute(stmt)
    await db.commit()
    return {"status": "ok", "id": notification_id}

@router.post("/mark-all-read")
async def mark_all_read(
    user_email: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    stmt = update(Notification)
    if user_email:
        stmt = stmt.where(Notification.user_email == user_email.lower())
    elif user_id:
        stmt = stmt.where(Notification.user_id == user_id)
    else:
        return {"status": "ok", "updated": 0}

    stmt = stmt.values(read=True)
    await db.execute(stmt)
    await db.commit()
    return {"status": "ok"}

@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: str,
    db: AsyncSession = Depends(get_db)
):
    stmt = delete(Notification).where(Notification.id == notification_id)
    await db.execute(stmt)
    await db.commit()
    return {"status": "ok", "id": notification_id}

@router.delete("")
async def clear_all_notifications(
    user_email: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    stmt = delete(Notification)
    if user_email:
        stmt = stmt.where(Notification.user_email == user_email.lower())
    elif user_id:
        stmt = stmt.where(Notification.user_id == user_id)
    else:
        return {"status": "ok", "deleted": 0}

    await db.execute(stmt)
    await db.commit()
    return {"status": "ok"}
