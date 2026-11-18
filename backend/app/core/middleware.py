"""
Enterprise Gateway Middleware for RoundMate AI.
Handles distributed trace correlation, latency instrumentation, tenant resolution, and structured audit logs.
"""

import time
import uuid
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.context import set_trace_id, set_tenant_id

logger = logging.getLogger("roundmate.gateway")


class EnterpriseGatewayMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # Ignore raw WebSocket handshakes from HTTP lifecycle interception
        if request.scope.get("type") != "http":
            return await call_next(request)

        # 1. Distributed Trace Correlation
        # Look for existing W3C traceparent, X-Trace-ID, or X-Request-ID
        incoming_trace = (
            request.headers.get("x-trace-id")
            or request.headers.get("x-request-id")
            or request.headers.get("traceparent")
        )
        trace_id = incoming_trace or f"rm-{uuid.uuid4().hex[:16]}"
        request.state.trace_id = trace_id
        t_token = set_trace_id(trace_id)

        # 2. Multi-tenant Scope Extraction
        tenant_id = request.headers.get("x-tenant-id", "default")
        tenant_token = set_tenant_id(tenant_id)

        # 3. High-resolution Request Latency Profiling
        start_time = time.perf_counter()
        
        try:
            response: Response = await call_next(request)
        except Exception:
            # Let global exception handlers deal with the error while guaranteeing context preservation
            raise
        finally:
            latency_ms = (time.perf_counter() - start_time) * 1000

        # 4. Attach Observability Headers
        response.headers["X-Trace-ID"] = trace_id
        response.headers["X-Response-Time-MS"] = f"{latency_ms:.2f}"

        # 5. Structured Audit Logging
        status_code = response.status_code
        method = request.method
        path = request.url.path
        
        if status_code >= 400:
            logger.warning(
                f"[Audit Error] {method} {path} status={status_code} "
                f"latency={latency_ms:.2f}ms trace_id={trace_id} tenant={tenant_id}"
            )
        else:
            logger.info(
                f"[Audit Access] {method} {path} status={status_code} "
                f"latency={latency_ms:.2f}ms trace_id={trace_id}"
            )

        return response

