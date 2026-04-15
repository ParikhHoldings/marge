"""Privacy policy helpers for classification, access, and safe serialization."""

from enum import Enum
from typing import Iterable, Optional

from fastapi import Header, HTTPException


class ConfidentialityClass(str, Enum):
    public = "public"
    private = "private"
    sensitive = "sensitive"


class AccessRole(str, Enum):
    public = "public"
    staff = "staff"
    pastor = "pastor"


_ROLE_LEVEL = {
    AccessRole.public: 0,
    AccessRole.staff: 1,
    AccessRole.pastor: 2,
}

_CONF_LEVEL = {
    ConfidentialityClass.public: 0,
    ConfidentialityClass.private: 1,
    ConfidentialityClass.sensitive: 2,
}


REDACTED = "[REDACTED]"


def normalize_confidentiality(value: str | ConfidentialityClass) -> ConfidentialityClass:
    if isinstance(value, ConfidentialityClass):
        return value
    if hasattr(value, "value"):
        value = value.value
    return ConfidentialityClass(str(value).lower())


def can_access(confidentiality: str | ConfidentialityClass, role: AccessRole) -> bool:
    conf = normalize_confidentiality(confidentiality)
    return _ROLE_LEVEL[role] >= _CONF_LEVEL[conf]


def redact_for_role(
    value,
    confidentiality: str | ConfidentialityClass,
    role: AccessRole,
    redacted_value=REDACTED,
):
    if value is None:
        return None
    return value if can_access(confidentiality, role) else redacted_value


def get_request_role(x_marge_role: Optional[str] = Header(default="pastor")) -> AccessRole:
    """Resolve caller role from request headers.

    Defaults to pastor for internal workflows; explicit role headers are required
    for lower-privilege/public outputs.
    """
    try:
        return AccessRole((x_marge_role or "pastor").lower())
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid role. Allowed roles: public, staff, pastor",
        ) from exc


def default_prayer_confidentiality(is_private: bool) -> ConfidentialityClass:
    return ConfidentialityClass.private if is_private else ConfidentialityClass.public


def assert_public_only(records: Iterable, field_name: str, context: str) -> None:
    """Guard that blocks accidental promotion of non-public records."""
    for record in records:
        value = getattr(record, field_name, None)
        if value is None:
            continue
        if normalize_confidentiality(value) != ConfidentialityClass.public:
            raise HTTPException(
                status_code=500,
                detail=f"Policy guard blocked non-public record in public {context} output",
            )
