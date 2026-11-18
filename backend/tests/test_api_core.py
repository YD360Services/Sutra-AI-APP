"""
Unit Tests for Core API Architecture Foundation:
- RFC 7807 Problem Details
- Distributed Tracing ContextVars
- Sliding Window Rate Limiter
"""

import unittest
import time
import uuid

from app.core.errors import (
    ProblemDetails,
    APIException,
    ResourceNotFoundException,
    UnauthorizedException,
    RateLimitExceededException
)
from app.core.context import (
    get_trace_id,
    set_trace_id,
    get_user_id,
    set_user_id,
    get_tenant_id,
    set_tenant_id
)
from app.core.rate_limiter import SlidingWindowRateLimiter


class TestAPICoreArchitecture(unittest.TestCase):

    def test_problem_details_schema(self):
        """Verifies RFC 7807 standard problem details schema."""
        problem = ProblemDetails(
            title="Resource Not Found",
            status=404,
            detail="Session identified by 'abc-123' was not found.",
            instance="/api/sessions/abc-123",
            error_code="SESSION_NOT_FOUND",
            trace_id="rm-test-trace-123"
        )
        data = problem.model_dump()
        self.assertEqual(data["status"], 404)
        self.assertEqual(data["title"], "Resource Not Found")
        self.assertEqual(data["error_code"], "SESSION_NOT_FOUND")
        self.assertEqual(data["trace_id"], "rm-test-trace-123")
        self.assertTrue("timestamp" in data)

    def test_typed_exceptions(self):
        """Verifies domain typed exceptions maintain correct HTTP statuses."""
        exc = ResourceNotFoundException("Resume", "res_001")
        self.assertEqual(exc.status_code, 404)
        self.assertEqual(exc.error_code, "RESOURCE_NOT_FOUND")

        unauth = UnauthorizedException("Token has expired.")
        self.assertEqual(unauth.status_code, 401)
        self.assertEqual(unauth.headers.get("WWW-Authenticate"), "Bearer")

        rate_limit = RateLimitExceededException(retry_after=45)
        self.assertEqual(rate_limit.status_code, 429)
        self.assertEqual(rate_limit.headers.get("Retry-After"), "45")

    def test_context_vars_isolation(self):
        """Verifies coroutine/thread-safe context propagation."""
        test_trace = f"rm-{uuid.uuid4().hex[:12]}"
        test_uid = uuid.uuid4()
        test_tenant = "tenant_enterprise_01"

        token_t = set_trace_id(test_trace)
        token_u = set_user_id(test_uid)
        token_ten = set_tenant_id(test_tenant)

        self.assertEqual(get_trace_id(), test_trace)
        self.assertEqual(get_user_id(), test_uid)
        self.assertEqual(get_tenant_id(), test_tenant)

    def test_sliding_window_rate_limiter(self):
        """Verifies sliding window rate limiting prevents bursts and recovers."""
        limiter = SlidingWindowRateLimiter()
        client_key = "test_ip_192.168.1.100"

        # Allow 3 requests in a 2-second window
        max_req = 3
        window = 2

        # 1st request -> Allowed
        allowed, remaining, _ = limiter.check(client_key, max_req, window)
        self.assertTrue(allowed)
        self.assertEqual(remaining, 2)

        # 2nd request -> Allowed
        allowed, remaining, _ = limiter.check(client_key, max_req, window)
        self.assertTrue(allowed)
        self.assertEqual(remaining, 1)

        # 3rd request -> Allowed
        allowed, remaining, _ = limiter.check(client_key, max_req, window)
        self.assertTrue(allowed)
        self.assertEqual(remaining, 0)

        # 4th request -> Denied (Rate limit hit)
        allowed, remaining, reset_sec = limiter.check(client_key, max_req, window)
        self.assertFalse(allowed)
        self.assertEqual(remaining, 0)
        self.assertGreaterEqual(reset_sec, 1)


if __name__ == "__main__":
    unittest.main()

