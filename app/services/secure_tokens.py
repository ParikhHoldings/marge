"""
Encrypted connector credential storage helpers for Marge integrations.

Marge may hold OAuth refresh tokens and workspace API-key connector payloads for
email, calendar, and church management systems. Those payloads must be encrypted
before they touch the database and must never be returned to the browser or chat.
"""

import json
import os
from typing import Any, Dict

from cryptography.fernet import Fernet, InvalidToken

ENCRYPTION_KEY_ENV = "MARGE_ENCRYPTION_KEY"


class SecureTokenConfigError(RuntimeError):
    """Raised when token encryption is not configured correctly."""


def encryption_key_is_configured() -> bool:
    key = os.getenv(ENCRYPTION_KEY_ENV)
    if not key:
        return False
    try:
        Fernet(key.encode("ascii"))
    except Exception:
        return False
    return True


def generate_encryption_key() -> str:
    """Return a new Fernet key suitable for MARGE_ENCRYPTION_KEY."""
    return Fernet.generate_key().decode("ascii")


def _fernet() -> Fernet:
    key = os.getenv(ENCRYPTION_KEY_ENV)
    if not key:
        raise SecureTokenConfigError(f"{ENCRYPTION_KEY_ENV} is required before connector credentials can be stored.")
    try:
        return Fernet(key.encode("ascii"))
    except Exception as exc:
        raise SecureTokenConfigError(f"{ENCRYPTION_KEY_ENV} must be a valid Fernet key.") from exc


def encrypt_token_payload(payload: Dict[str, Any]) -> str:
    plaintext = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _fernet().encrypt(plaintext).decode("ascii")


def decrypt_token_payload(ciphertext: str) -> Dict[str, Any]:
    try:
        plaintext = _fernet().decrypt(ciphertext.encode("ascii"))
    except InvalidToken as exc:
        raise SecureTokenConfigError("Stored token payload could not be decrypted with the configured key.") from exc
    return json.loads(plaintext.decode("utf-8"))
