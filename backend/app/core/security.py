from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Union
import base64
import hashlib
import hmac
import json
import uuid

try:
    from jose import jwt as jose_jwt
except ImportError:
    jose_jwt = None

try:
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
except Exception:
    pwd_context = None

from app.core.config import settings


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if pwd_context:
        return pwd_context.verify(plain_password, hashed_password)
    return hmac.compare_digest(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    if pwd_context:
        return pwd_context.hash(password)
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_access_token(
    subject: Union[str, Any],
    expires_delta: Optional[timedelta] = None,
    tenant_id: Optional[str] = None,
) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode: Dict[str, Any] = {
        "exp": int(expire.timestamp()),
        "iat": int(now.timestamp()),
        "sub": str(subject),
        "jti": str(uuid.uuid4()),
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
    }
    if tenant_id:
        to_encode["tid"] = str(tenant_id)

    if jose_jwt is not None:
        return jose_jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.ALGORITHM)

    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    payload = _b64url_encode(json.dumps(to_encode, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header}.{payload}".encode("ascii")
    sig = hmac.new(settings.JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url_encode(sig)}"


def decode_token_claims(token: str) -> Optional[Dict[str, Any]]:
    """
    Validates JWT signature, algorithm, expiry, issuer, and audience.
    Returns decoded claims dict on success, or None if invalid/expired.
    """
    if not token or token.count(".") != 2:
        return None
    try:
        if jose_jwt is not None:
            claims = jose_jwt.decode(
                token,
                settings.JWT_SECRET,
                algorithms=[settings.ALGORITHM],
                options={"verify_aud": False, "verify_iss": False},
            )
        else:
            header_b64, payload_b64, sig_b64 = token.split(".")
            header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
            if header.get("alg") != settings.ALGORITHM:
                return None
            signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
            expected_sig = hmac.new(settings.JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
            if not hmac.compare_digest(_b64url_decode(sig_b64), expected_sig):
                return None
            claims = json.loads(_b64url_decode(payload_b64).decode("utf-8"))

        # Validate expiry
        exp = claims.get("exp")
        if exp is None or float(exp) < datetime.now(timezone.utc).timestamp():
            return None

        # Validate issuer/audience when present on token
        if "iss" in claims and claims["iss"] != settings.JWT_ISSUER:
            return None
        if "aud" in claims and claims["aud"] != settings.JWT_AUDIENCE:
            return None

        return claims
    except Exception:
        return None


def decode_access_token(token: str) -> Optional[str]:
    claims = decode_token_claims(token)
    return claims.get("sub") if claims else None


async def revoke_token_jti(jti: str, ttl_seconds: int = 86400) -> None:
    from app.cache.redis import redis_cache
    if jti:
        await redis_cache.set_cached_item(f"rm:revoked_jti:{jti}", "1", expire_seconds=ttl_seconds)


async def is_token_revoked(jti: Optional[str]) -> bool:
    if not jti:
        return False
    from app.cache.redis import redis_cache
    val = await redis_cache.get_cached_item(f"rm:revoked_jti:{jti}")
    return val == "1"
