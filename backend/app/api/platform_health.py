"""
Platform health: the latest manually recorded check for each supported interview platform.

GET    /api/platform-health                 public; what the dashboard shows every user
PUT    /api/platform-health/{platform_id}   admin only; record / replace a check
DELETE /api/platform-health/{platform_id}   admin only; clear it (back to "not reported")

There is no automated platform testing. Rows exist only when an admin records a check, and
platforms without a row are reported as "not reported" by the client. Nothing is defaulted.

Admin writes are authenticated with the caller's server-issued session token
(`Authorization: Bearer <login_token>`, the value returned by /api/auth/google and stored by
the web app as `login-token`). The token must match a user's active_session_token, and that
user's email must be in the admin_emails table. The X-Admin-Email header used elsewhere is
NOT accepted here because any client can set it.
"""
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.database import get_db
from app.db.models import AdminEmail, PlatformHealthCheck, User

router = APIRouter()
logger = logging.getLogger("roundmate.platform_health")

# Must match PLATFORM_CATALOG in the web app (src/utils/platformHealth.ts).
SUPPORTED_PLATFORMS = ("zoom", "google-meet", "teams", "hackerrank", "leetcode", "codesignal", "coderpad", "webex")

PlatformStatus = Literal["operational", "warning", "failed", "testing"]

MAX_TEXT = 1000
MAX_LATENCY_MS = 600_000  # 10 minutes; anything larger is a typo
FUTURE_SKEW = timedelta(minutes=5)


class PlatformCheckIn(BaseModel):
    status: PlatformStatus
    checkedAt: Optional[datetime] = None
    result: Optional[str] = Field(default=None, max_length=MAX_TEXT)
    latencyMs: Optional[int] = Field(default=None, ge=0, le=MAX_LATENCY_MS)
    error: Optional[str] = Field(default=None, max_length=MAX_TEXT)
    troubleshooting: Optional[str] = Field(default=None, max_length=MAX_TEXT)

    @field_validator("result", "error", "troubleshooting")
    @classmethod
    def _blank_to_none(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("checkedAt")
    @classmethod
    def _to_naive_utc(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is None:
            return None
        if v.tzinfo is not None:
            v = v.astimezone(timezone.utc).replace(tzinfo=None)
        if v > datetime.utcnow() + FUTURE_SKEW:
            raise ValueError("checkedAt cannot be in the future")
        return v

    @model_validator(mode="after")
    def _completed_checks_need_time(self):
        # "testing" can be in progress; every completed outcome must say when it was checked.
        if self.status != "testing" and self.checkedAt is None:
            raise ValueError("checkedAt is required unless status is 'testing'")
        return self


class PlatformCheckOut(BaseModel):
    id: str
    status: PlatformStatus
    checkedAt: Optional[str] = None
    result: Optional[str] = None
    latencyMs: Optional[int] = None
    error: Optional[str] = None
    troubleshooting: Optional[str] = None
    updatedAt: Optional[str] = None


def _iso(dt: Optional[datetime]) -> Optional[str]:
    # Stored as naive UTC; make that explicit for clients.
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def _to_out(row: PlatformHealthCheck) -> PlatformCheckOut:
    return PlatformCheckOut(
        id=row.platform_id,
        status=row.status,  # type: ignore[arg-type]
        checkedAt=_iso(row.checked_at),
        result=row.result,
        latencyMs=row.latency_ms,
        error=row.error,
        troubleshooting=row.troubleshooting,
        updatedAt=_iso(row.updated_at),
    )


def _require_supported(platform_id: str) -> str:
    pid = platform_id.strip().lower()
    if pid not in SUPPORTED_PLATFORMS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown platform")
    return pid


async def require_admin_session(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> str:
    """Resolve the caller from their session token and require an admin email. Returns the email."""
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sign in again to manage platform health.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthorized
    token = authorization[7:].strip()
    # Session tokens are UUIDs (36 chars); reject trivially short values outright.
    if len(token) < 16:
        raise unauthorized

    result = await db.execute(select(User).where(User.active_session_token == token))
    user = result.scalars().first()
    if not user or not user.email:
        raise unauthorized

    email = user.email.lower().strip()
    admin = await db.execute(select(AdminEmail).where(AdminEmail.email.ilike(email)))
    if admin.scalar_one_or_none() is None:
        # admin_emails is seeded lazily (GET /api/admins). Until then, fall back to the configured seed list.
        any_admin = await db.execute(select(AdminEmail.id).limit(1))
        seeds = {e.strip().lower() for e in getattr(settings, "INITIAL_ADMIN_EMAILS", "").split(",") if e.strip()}
        if any_admin.first() is not None or email not in seeds:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return email


@router.get("/platform-health", response_model=List[PlatformCheckOut])
async def list_platform_health(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PlatformHealthCheck))
    rows = [r for r in result.scalars().all() if r.platform_id in SUPPORTED_PLATFORMS]
    order = {pid: i for i, pid in enumerate(SUPPORTED_PLATFORMS)}
    rows.sort(key=lambda r: order[r.platform_id])
    return [_to_out(r) for r in rows]


@router.put("/platform-health/{platform_id}", response_model=PlatformCheckOut)
async def record_platform_check(
    platform_id: str,
    payload: PlatformCheckIn,
    admin_email: str = Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
):
    pid = _require_supported(platform_id)
    row = await db.get(PlatformHealthCheck, pid)
    if row is None:
        row = PlatformHealthCheck(platform_id=pid, status=payload.status)
        db.add(row)
    # Replace the whole record so stale error/latency from an older check never lingers.
    row.status = payload.status
    row.checked_at = payload.checkedAt
    row.result = payload.result
    row.latency_ms = payload.latencyMs
    row.error = payload.error
    row.troubleshooting = payload.troubleshooting
    row.updated_by = admin_email
    row.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(row)
    logger.info("Platform check recorded: %s=%s by %s", pid, payload.status, admin_email)
    return _to_out(row)


@router.delete("/platform-health/{platform_id}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_platform_check(
    platform_id: str,
    admin_email: str = Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
):
    pid = _require_supported(platform_id)
    row = await db.get(PlatformHealthCheck, pid)
    if row is not None:
        await db.delete(row)
        await db.commit()
        logger.info("Platform check cleared: %s by %s", pid, admin_email)
    return None
