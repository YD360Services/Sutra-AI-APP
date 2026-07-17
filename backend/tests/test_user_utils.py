import uuid

from app.core.user_utils import normalize_user_id


def test_valid_uuid_is_preserved():
    value = "123e4567-e89b-12d3-a456-426614174000"
    assert normalize_user_id(value) == uuid.UUID(value)


def test_invalid_string_is_normalized_to_uuid():
    value = "4hRBcvdmloZQUQge7wAunvopKuz2"
    normalized = normalize_user_id(value)
    assert normalized is not None
    assert isinstance(normalized, uuid.UUID)
    assert normalize_user_id(value) == normalized
