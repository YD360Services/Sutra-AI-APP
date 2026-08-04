import uuid
from typing import Optional


def normalize_user_id(user_id: Optional[str]) -> Optional[uuid.UUID]:
    if not user_id:
        return None

    if isinstance(user_id, uuid.UUID):
        return user_id

    value = str(user_id).strip()
    if not value:
        return None

    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        # Fallback for legacy/mock/firebase identifiers that are not strict UUIDs.
        # Use a deterministic UUID derived from the incoming string.
        # We try to match the 'firebase:' prefix mapping used in auth.py
        return uuid.uuid5(uuid.NAMESPACE_DNS, f"firebase:{value}")
