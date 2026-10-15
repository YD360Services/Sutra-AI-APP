import json
from datetime import datetime
from typing import List, Optional, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import SupportTicket

router = APIRouter(prefix="/tickets", tags=["Support Tickets"])

class TicketMessage(BaseModel):
    id: str
    sender: str  # 'user' | 'admin' | 'support'
    senderName: Optional[str] = "User"
    text: str
    attachment: Optional[str] = None
    time: str

class TicketCreate(BaseModel):
    id: str
    userEmail: str
    userName: str = "Anonymous"
    userPhoto: Optional[str] = None
    subject: str
    category: str = "General"
    status: str = "Open"
    priority: str = "Medium"
    createdTime: str = "Recently"
    messages: List[TicketMessage] = []

class TicketReply(BaseModel):
    message: TicketMessage
    status: Optional[str] = None

class TicketStatusUpdate(BaseModel):
    status: str

@router.get("")
async def list_tickets(
    user_email: Optional[str] = Query(None),
    is_admin: bool = Query(False),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(SupportTicket)
    if not is_admin and user_email:
        stmt = stmt.where(SupportTicket.user_email == user_email.lower())
    
    stmt = stmt.order_by(SupportTicket.updated_at.desc())
    result = await db.execute(stmt)
    tickets = result.scalars().all()

    out = []
    for t in tickets:
        msgs = []
        try:
            msgs = json.loads(t.messages_json or "[]")
        except Exception:
            msgs = []
        out.append({
            "id": t.id,
            "userEmail": t.user_email,
            "userName": t.user_name,
            "userPhoto": t.user_photo,
            "subject": t.subject,
            "category": t.category,
            "status": t.status,
            "priority": t.priority,
            "createdTime": t.created_time,
            "createdAt": int(t.created_at.timestamp() * 1000) if t.created_at else 0,
            "updatedAt": int(t.updated_at.timestamp() * 1000) if t.updated_at else 0,
            "messages": msgs
        })
    return out

@router.post("")
async def create_or_upsert_ticket(
    payload: TicketCreate,
    db: AsyncSession = Depends(get_db)
):
    stmt = select(SupportTicket).where(SupportTicket.id == payload.id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    msgs_json = json.dumps([m.dict() for m in payload.messages])

    if existing:
        existing.user_email = payload.userEmail.lower()
        existing.user_name = payload.userName
        existing.user_photo = payload.userPhoto
        existing.subject = payload.subject
        existing.category = payload.category
        existing.status = payload.status
        existing.priority = payload.priority
        existing.messages_json = msgs_json
        existing.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(existing)
        return {"status": "ok", "id": existing.id}
    else:
        ticket = SupportTicket(
            id=payload.id,
            user_email=payload.userEmail.lower(),
            user_name=payload.userName,
            user_photo=payload.userPhoto,
            subject=payload.subject,
            category=payload.category,
            status=payload.status,
            priority=payload.priority,
            created_time=payload.createdTime,
            messages_json=msgs_json,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(ticket)
        await db.commit()
        await db.refresh(ticket)
        return {"status": "ok", "id": ticket.id}

@router.post("/{ticket_id}/reply")
async def add_reply(
    ticket_id: str,
    payload: TicketReply,
    db: AsyncSession = Depends(get_db)
):
    stmt = select(SupportTicket).where(SupportTicket.id == ticket_id)
    result = await db.execute(stmt)
    ticket = result.scalar_one_or_none()

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    msgs = []
    try:
        msgs = json.loads(ticket.messages_json or "[]")
    except Exception:
        msgs = []

    msgs.append(payload.message.dict())
    ticket.messages_json = json.dumps(msgs)
    if payload.status:
        ticket.status = payload.status
    elif payload.message.sender == "user":
        ticket.status = "Open"

    ticket.updated_at = datetime.utcnow()
    await db.commit()
    return {"status": "ok", "id": ticket_id}

@router.patch("/{ticket_id}/status")
async def update_status(
    ticket_id: str,
    payload: TicketStatusUpdate,
    db: AsyncSession = Depends(get_db)
):
    stmt = select(SupportTicket).where(SupportTicket.id == ticket_id)
    result = await db.execute(stmt)
    ticket = result.scalar_one_or_none()

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ticket.status = payload.status
    ticket.updated_at = datetime.utcnow()
    await db.commit()
    return {"status": "ok", "id": ticket_id, "new_status": payload.status}
