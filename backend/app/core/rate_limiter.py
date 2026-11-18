"""
Distributed-ready Sliding Window Rate Limiter for RoundMate AI.
Protects AI endpoints and authentication routes against denial-of-wallet and brute-force attacks.
"""

import time
import threading
from typing import Dict, List, Tuple, Optional
from fastapi import Request, Response
import logging

from app.core.errors import RateLimitExceededException

logger = logging.getLogger("roundmate.ratelimit")


class SlidingWindowRateLimiter:
    """
    Thread-safe in-memory sliding window rate limiter.
    Stores timestamps of requests per client key.
    Automatically evicts expired timestamps on evaluation.
    """
    def __init__(self):
        self._lock = threading.Lock()
        # key -> list of float timestamps
        self._windows: Dict[str, List[float]] = {}
        # Track last sweep to clean up dead keys periodically
        self._last_sweep = time.monotonic()

    def _sweep_stale_keys(self, now: float, window_seconds: float):
        """Removes empty keys to prevent unbounded memory growth."""
        if now - self._last_sweep < 60:
            return
        self._last_sweep = now
        stale_keys = [k for k, timestamps in self._windows.items() if not timestamps or (now - timestamps[-1]) > window_seconds]
        for k in stale_keys:
            del self._windows[k]

    def check(self, key: str, max_requests: int, window_seconds: int = 60) -> Tuple[bool, int, int]:
        """
        Check if a request is allowed.
        Returns:
            (is_allowed: bool, remaining_requests: int, reset_seconds: int)
        """
        now = time.monotonic()
        cutoff = now - window_seconds

        with self._lock:
            self._sweep_stale_keys(now, float(window_seconds))
            
            timestamps = self._windows.setdefault(key, [])
            # Filter out timestamps older than the sliding window
            self._windows[key] = [t for t in timestamps if t > cutoff]
            current_count = len(self._windows[key])

            if current_count >= max_requests:
                oldest = self._windows[key][0]
                reset_seconds = max(1, int(window_seconds - (now - oldest)))
                return False, 0, reset_seconds

            self._windows[key].append(now)
            remaining = max(0, max_requests - (current_count + 1))
            return True, remaining, window_seconds


# Global singleton instance
rate_limiter = SlidingWindowRateLimiter()


def rate_limit(max_requests: int = 60, window_seconds: int = 60, key_prefix: str = "global"):
    """
    FastAPI dependency for rate limiting endpoints.
    Usage:
        @router.post("/stream", dependencies=[Depends(rate_limit(max_requests=20, window_seconds=60))])
    """
    async def dependency(request: Request, response: Response):
        # Prefer authenticated user ID, fallback to client IP
        client_ip = request.client.host if request.client else "unknown"
        auth_header = request.headers.get("authorization", "")
        identifier = auth_header[-16:] if len(auth_header) >= 16 else client_ip
        
        limit_key = f"{key_prefix}:{identifier}"
        allowed, remaining, reset_sec = rate_limiter.check(limit_key, max_requests, window_seconds)

        response.headers["X-RateLimit-Limit"] = str(max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_sec)

        if not allowed:
            logger.warning(f"Rate limit exceeded for key {limit_key} on {request.url.path}")
            raise RateLimitExceededException(
                detail=f"Too many requests. Limit is {max_requests} per {window_seconds}s.",
                retry_after=reset_sec
            )

    return dependency
