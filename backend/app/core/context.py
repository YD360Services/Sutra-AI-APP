"""
Thread-safe and Coroutine-safe Context Propagation for RoundMate AI.
Enforces multi-tenant scoping and distributed trace correlation across the entire execution lifecycle.
"""

from contextvars import ContextVar
from typing import Optional
import uuid

# Distributed Trace Correlation
current_trace_id: ContextVar[str] = ContextVar("current_trace_id", default="")

# Multi-Tenant & User Scope
current_user_id: ContextVar[Optional[uuid.UUID]] = ContextVar("current_user_id", default=None)
current_tenant_id: ContextVar[str] = ContextVar("current_tenant_id", default="default")

def get_trace_id() -> str:
    """Get the active trace ID or return an empty string."""
    return current_trace_id.get()

def set_trace_id(trace_id: str):
    """Set the active trace ID for the current async task context."""
    return current_trace_id.set(trace_id)

def get_user_id() -> Optional[uuid.UUID]:
    """Get the active authenticated user ID."""
    return current_user_id.get()

def set_user_id(user_id: Optional[uuid.UUID]):
    """Set the active user ID for the current async task context."""
    return current_user_id.set(user_id)

def get_tenant_id() -> str:
    """Get the active tenant / workspace identifier."""
    return current_tenant_id.get()

def set_tenant_id(tenant_id: str):
    """Set the active tenant ID for multi-tenant data isolation."""
    return current_tenant_id.set(tenant_id)

