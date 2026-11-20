from fastapi import Depends, HTTPException, status, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
import uuid
import logging

from app.core.config import settings
from app.core.security import decode_access_token
from app.core.context import set_user_id
from app.db.database import get_db
from app.db.models import User, AdminEmail

logger = logging.getLogger("roundmate.deps")
security_bearer = HTTPBearer(auto_error=False)


async def _user_from_session_token(token: str, db: AsyncSession) -> Optional[User]:
    # Session tokens are UUIDs; reject trivially short values without touching the DB.
    if len(token) < 16:
        return None
    result = await db.execute(select(User).where(User.active_session_token == token))
    user = result.scalars().first()
    if user:
        set_user_id(user.id)
    return user

async def get_current_user_optional(
    auth_creds: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    """
    Extract and verify JWT bearer token from Authorization header.
    Returns User if valid, None if missing or invalid.
    """
    token = None
    if auth_creds and auth_creds.credentials:
        token = auth_creds.credentials
    elif authorization:
        parts = authorization.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]
        elif len(parts) == 1:
            token = parts[0]

    if not token:
        return None

    try:
        user_id_str = decode_access_token(token)
        if not user_id_str:
            # Not a JWT: the web app and desktop authenticate with the per-login session token
            # (`login-token`, stored on the user as active_session_token). Nothing issues JWTs
            # today, so without this every protected route returned 401.
            return await _user_from_session_token(token, db)

        # Convert to UUID
        try:
            user_uuid = uuid.UUID(user_id_str)
        except ValueError:
            return None

        user = await db.get(User, user_uuid)
        if user:
            set_user_id(user.id)
        return user
    except Exception as e:
        logger.warning(f"Failed to authenticate token: {e}")
        return None

async def get_current_user(
    current_user: Optional[User] = Depends(get_current_user_optional)
) -> User:
    """
    Enforces authentication on protected routes.
    Raises 401 Unauthorized if token is missing or invalid.
    """
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please provide a valid Bearer token.",
            headers={"WWW-Authenticate": "Bearer"}
        )
    return current_user

async def require_admin_user(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Enforces admin privileges on administrative endpoints.
    Verifies caller email against admin database table and config seed.
    """
    email = (current_user.email or "").strip().lower()
    if not email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Admin authorization required."
        )

    # 1. Check in database admin_emails table
    result = await db.execute(select(AdminEmail).where(AdminEmail.email.ilike(email)))
    admin_record = result.scalar_one_or_none()
    if admin_record:
        return current_user

    # 2. Check in INITIAL_ADMIN_EMAILS settings
    initial_admins = [
        e.strip().lower() 
        for e in getattr(settings, "INITIAL_ADMIN_EMAILS", "").split(",") 
        if e.strip()
    ]
    if email in initial_admins:
        return current_user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Access forbidden: You do not have administrator permissions."
    )

