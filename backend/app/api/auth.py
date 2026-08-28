from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional
import uuid
import logging
import time

from app.db.database import get_db
from app.db.repositories import UserRepository
from app.cache.redis import redis_cache

router = APIRouter()
logger = logging.getLogger("copilotx.auth")

class GoogleAuthRequest(BaseModel):
    firebase_uid: str
    email: str
    name: Optional[str] = None
    access_token: Optional[str] = None
    is_mock: Optional[bool] = False
    login_token: Optional[str] = None
    device_type: Optional[str] = None
    force: Optional[bool] = False

class GoogleAuthResponse(BaseModel):
    id: uuid.UUID
    email: str
    name: Optional[str]
    created_at: str

    class Config:
        from_attributes = True

@router.post("/auth/google")
async def google_auth(
    payload: GoogleAuthRequest,
    db: AsyncSession = Depends(get_db)
):
    user_repo = UserRepository(db)
    
    # If a real access token is provided, verify it with Google API
    if payload.access_token and not payload.is_mock:
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                res = await client.get(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    params={"access_token": payload.access_token}
                )
                if res.status_code == 200:
                    info = res.json()
                    # Securely override values with verified info from Google API
                    payload.firebase_uid = info.get("sub", payload.firebase_uid)
                    payload.email = info.get("email", payload.email)
                    if info.get("name"):
                        payload.name = info.get("name")
                else:
                    logger.error(f"Google Token Verification failed: {res.status_code} - {res.text}")
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid Google Access Token"
                    )
        except Exception as e:
            if isinstance(e, HTTPException):
                raise e
            logger.error(f"Error validating Google token: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Google Authentication verification failed: {str(e)}"
            )
            
    # Generate a deterministic UUID from the Firebase UID
    user_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"firebase:{payload.firebase_uid}")
    
    logger.info(f"Authenticating user {payload.email} with Firebase UID {payload.firebase_uid} -> Mapped UUID {user_id}")
    
    # Check if user exists
    user = await user_repo.get_by_id(user_id)
    if not user:
        # Check by email as fallback
        user_by_email = await user_repo.get_by_email(payload.email)
        if user_by_email:
            user = user_by_email
            logger.info(f"User found by email {payload.email}, but ID does not match. Linking to ID: {user.id}")
        else:
            # Create user
            logger.info(f"User {payload.email} not found. Creating new user record in DB with ID: {user_id}")
            user = await user_repo.create(
                id=user_id,
                email=payload.email,
                password_hash="firebase_authenticated",
                name=payload.name
            )
            # Commit handled by the async session dependency or save manually
            await db.commit()
    
    user_key = f"active_login:{str(user.id)}"
    device_key = f"active_login_device:{str(user.id)}"
    
    if payload.login_token:
        # Explicit login: assign new token and overwrite active session
        login_token = payload.login_token
        user.active_session_token = login_token
        user.active_device_type = payload.device_type or "Desktop / Laptop"
        user.last_active_at = datetime.utcnow()
        await db.commit()
        await redis_cache.set_cached_item(user_key, login_token, expire_seconds=86400)
        if payload.device_type:
            await redis_cache.set_cached_item(device_key, payload.device_type, expire_seconds=86400)
        logger.info(f"[LOGIN] Active session claimed for {user.email}: token={login_token[:8]}... device={user.active_device_type}")
    else:
        # App reload / sync — return existing token WITHOUT overwriting
        login_token = user.active_session_token
        if not login_token:
            login_token = await redis_cache.get_cached_item(user_key)
        if not login_token:
            login_token = str(uuid.uuid4())
            user.active_session_token = login_token
            user.last_active_at = datetime.utcnow()
            await db.commit()
            await redis_cache.set_cached_item(user_key, login_token, expire_seconds=86400)
        logger.info(f"[SYNC] Returning existing session token for {user.email}: token={login_token[:8] if login_token else 'none'}...")
    
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "created_at": user.created_at.isoformat(),
        "login_token": login_token
    }


class CheckExistingSessionRequest(BaseModel):
    firebase_uid: str
    email: Optional[str] = None
    current_token: Optional[str] = None
    device_type: Optional[str] = None

@router.post("/auth/check-existing-session")
async def check_existing_session(
    payload: CheckExistingSessionRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Checks if a user already has an active session on another device before completing sign-in.
    Does not issue or alter any active tokens.
    """
    user_repo = UserRepository(db)
    user_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"firebase:{payload.firebase_uid}")
    user = await user_repo.get_by_id(user_id)
    if not user and payload.email:
        user = await user_repo.get_by_email(payload.email)
    
    if not user:
        return {
            "has_conflict": False,
            "user_id": str(user_id)
        }
    
    existing_token = user.active_session_token
    if not existing_token:
        user_key = f"active_login:{str(user.id)}"
        existing_token = await redis_cache.get_cached_item(user_key)
    
    if not existing_token:
        return {
            "has_conflict": False,
            "user_id": str(user.id)
        }
    
    # If the request originates from the same device / current token, it's not a conflict
    if payload.current_token and existing_token == payload.current_token:
        return {
            "has_conflict": False,
            "user_id": str(user.id)
        }
        
    existing_device = user.active_device_type or "Another Device"
    
    return {
        "has_conflict": True,
        "user_id": str(user.id),
        "existing_device": existing_device,
        "email": user.email
    }


class ForceLogoutRequest(BaseModel):
    user_id: Optional[str] = None
    firebase_uid: Optional[str] = None
    email: Optional[str] = None

@router.post("/auth/force-logout")
async def force_logout(
    payload: ForceLogoutRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Invalidates any existing active session tokens for the given user,
    allowing the new device to sign in cleanly.
    """
    user_repo = UserRepository(db)
    user = None
    if payload.user_id:
        try:
            user = await user_repo.get_by_id(uuid.UUID(payload.user_id))
        except Exception:
            pass
    if not user and payload.firebase_uid:
        user_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"firebase:{payload.firebase_uid}")
        user = await user_repo.get_by_id(user_id)
    if not user and payload.email:
        user = await user_repo.get_by_email(payload.email)

    if user:
        user.active_session_token = None
        await db.commit()
        user_key = f"active_login:{str(user.id)}"
        device_key = f"active_login_device:{str(user.id)}"
        await redis_cache.delete_cached_item(user_key)
        await redis_cache.delete_cached_item(device_key)
        logger.info(f"Force-logout executed for user {user.email} - active session revoked.")
        return {"success": True, "message": "Previous session invalidated"}
        
    return {"success": False, "message": "User identifier required"}


class CheckSessionRequest(BaseModel):
    user_id: str
    login_token: str

@router.post("/auth/check-session")
async def check_session(
    payload: CheckSessionRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Validates whether the current login_token is still the active one in DB.
    Returns {"valid": true} if still active, {"valid": false} if another
    login has taken over.
    """
    user_repo = UserRepository(db)
    user = None
    try:
        user_uuid = uuid.UUID(payload.user_id)
        user = await user_repo.get_by_id(user_uuid)
    except Exception:
        pass
        
    if not user:
        user = await user_repo.get_by_email(payload.user_id)
        
    if not user:
        # Fallback to in-memory/cache
        user_key = f"active_login:{payload.user_id}"
        stored_token = await redis_cache.get_cached_item(user_key)
        if not stored_token:
            return {"valid": True}
        return {"valid": stored_token == payload.login_token}
        
    if not user.active_session_token:
        # First check, record token
        user.active_session_token = payload.login_token
        user.last_active_at = datetime.utcnow()
        await db.commit()
        return {"valid": True}
        
    is_valid = user.active_session_token == payload.login_token
    if not is_valid:
        logger.info(f"Session check FAILED for user {user.email} — token mismatch (kicked by newer login on {user.active_device_type or 'other device'})")
    else:
        user.last_active_at = datetime.utcnow()
        await db.commit()
        
    return {"valid": is_valid, "device": user.active_device_type or "Another Device"}


from fastapi.responses import HTMLResponse

@router.get("/auth/sync", response_class=HTMLResponse)
async def sync_page(port: int = 48999):
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Stealth AI - Sync Account</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {
                background: #09090e;
                color: #fff;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                display: flex;
                align-items: center;
                justify-content: center;
                height: 100vh;
                margin: 0;
                overflow: hidden;
            }
            .card {
                background: rgba(15, 15, 22, 0.96);
                backdrop-filter: blur(12px);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 16px;
                padding: 40px;
                text-align: center;
                max-width: 400px;
                box-shadow: 0 20px 50px rgba(0, 0, 0, 0.5), inset 0 1px 1px rgba(255, 255, 255, 0.05);
                position: relative;
            }
            .logo {
                width: 48px;
                height: 48px;
                background: linear-gradient(135deg, #a78bfa, #7c3aed);
                border-radius: 12px;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                margin-bottom: 20px;
                box-shadow: 0 8px 24px rgba(124, 58, 237, 0.4);
            }
            h1 { font-size: 20px; font-weight: 700; margin: 0 0 10px 0; color: #fff; }
            p { font-size: 13.5px; color: #9ca3af; line-height: 1.6; margin: 0 0 30px 0; }
            .btn {
                background: linear-gradient(135deg, #8b5cf6, #6d28d9);
                border: none;
                color: #fff;
                padding: 12px 28px;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 600;
                cursor: pointer;
                box-shadow: 0 4px 15px rgba(139, 92, 246, 0.4);
                transition: all 0.2s ease;
                outline: none;
                width: 100%;
                box-sizing: border-box;
            }
            .btn:hover {
                transform: translateY(-1px);
                box-shadow: 0 6px 20px rgba(139, 92, 246, 0.5);
            }
            .btn:active {
                transform: translateY(1px);
            }
        </style>
    </head>
    <body>
        <div class="card">
            <div class="logo">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.5">
                    <path d="M23 12a11 11 0 1 1-2.07-6.56L23 8"></path>
                    <path d="M17 8h6V2"></path>
                </svg>
            </div>
            <h1>Link Desktop Application</h1>
            <p>Would you like to sync the Stealth Desktop application with your web account session?</p>
            <button class="btn" onclick="syncApp()">Authorize Desktop App</button>
        </div>
        <script>
            function syncApp() {
                const port = """ + str(port) + """;
                // Standard default user details for auth mock
                const email = 'premium@stealth.ai';
                const token = 'mock-secure-token-12345';
                
                fetch(`http://127.0.0.1:${port}/auth-callback?email=${email}&token=${token}`)
                    .then(res => res.json())
                    .then(data => {
                        if (data.success) {
                            document.querySelector('.card').innerHTML = `
                                <div class="logo" style="background: linear-gradient(135deg, #10b981, #059669); box-shadow: 0 8px 24px rgba(16, 185, 129, 0.4);">
                                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3">
                                        <polyline points="20 6 9 17 4 12"></polyline>
                                    </svg>
                                </div>
                                <h1>Successfully Synced!</h1>
                                <p style="margin-bottom: 0;">You can now close this browser window and return to the Stealth Desktop Application.</p>
                            `;
                        }
                    })
                    .catch(err => {
                        alert('Error: Could not link app. Please make sure the Stealth desktop application is running on your machine.');
                    });
            }
        </script>
    </body>
    </html>
    """
    return html_content

