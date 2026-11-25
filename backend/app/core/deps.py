from fastapi import Depends, HTTPException, status, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
import uuid
import logging

from app.core.config import settings
from app.core.security import decode_access_token, decode_token_claims, is_token_revoked
from app.core.context import set_user_id, set_tenant_id
from app.db.database import get_db
from app.db.models import User, AdminEmail, Session as SessionModel

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
        claims = decode_token_claims(token)
        if claims:
            if await is_token_revoked(claims.get("jti")):
                return None
            user_id_str = claims.get("sub")
            if claims.get("tid"):
                set_tenant_id(str(claims["tid"]))
        else:
            user_id_str = decode_access_token(token)

        if not user_id_str:
            # Not a JWT: the web app and desktop authenticate with the per-login session token
            # (`login-token`, stored on the user as active_session_token).
            return await _user_from_session_token(token, db)

        # Convert to UUID
        try:
            user_uuid = uuid.UUID(user_id_str)
        except ValueError:
            return None

        user = await db.get(User, user_uuid)
        if user:
            set_user_id(user.id)
            if not claims or not claims.get("tid"):
                set_tenant_id(str(user.id))
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


class PrincipalContext:
    """Resolved authenticated principal bound to user & tenant scope."""
    def __init__(self, user_id: uuid.UUID, tenant_id: str, email: str, plan: str, is_admin: bool = False):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.email = email
        self.plan = plan
        self.is_admin = is_admin


def _is_admin_email(email: str) -> bool:
    clean = (email or "").strip().lower()
    if not clean:
        return False
    initial_admins = [
        e.strip().lower()
        for e in getattr(settings, "INITIAL_ADMIN_EMAILS", "").split(",")
        if e.strip()
    ]
    return clean in initial_admins


async def get_principal_context(
    current_user: User = Depends(get_current_user),
) -> PrincipalContext:
    """Never trusts client-supplied tenant_id/user_id; resolves from verified credential."""
    return PrincipalContext(
        user_id=current_user.id,
        tenant_id=str(current_user.id),
        email=current_user.email or "",
        plan=current_user.plan or "Free",
        is_admin=_is_admin_email(current_user.email or ""),
    )


async def verify_session_ownership(
    session_id: uuid.UUID,
    current_user: Optional[User],
    db: AsyncSession,
    allow_anonymous_legacy: bool = False,
) -> SessionModel:
    """
    Centralized session ownership & tenant isolation guard (Item 7).
    Raises 404 if session doesn't exist, 403 if owned by a different user.
    """
    session_obj = await db.get(SessionModel, session_id)
    if not session_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    if session_obj.user_id is None and allow_anonymous_legacy:
        return session_obj

    if not current_user:
        if allow_anonymous_legacy:
            return session_obj
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    if session_obj.user_id != current_user.id and not _is_admin_email(current_user.email or ""):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: You do not own this interview session."
        )
    return session_obj

