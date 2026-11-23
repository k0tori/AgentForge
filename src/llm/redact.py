"""Remove credentials from text that might be stored or logged."""
from __future__ import annotations

import os
import re

from src.config import settings

_SK = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")
_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._\-]{6,}", re.IGNORECASE)
_URL_PASSWORD = re.compile(r"(://[^:/\s]+:)([^@/\s]+)@")
_MAX_LEN = 500


def sanitize_payload(value: object) -> object:
    """Sanitize every string in a JSON-like value before it is stored or streamed."""
    if isinstance(value, str):
        return sanitize(value)
    if isinstance(value, dict):
        return {str(key): sanitize_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_payload(item) for item in value]
    return value


def sanitize(text: str) -> str:
    """Return text with known secrets removed. The result is capped."""
    redacted = text or ""
    key = settings.DEEPSEEK_API_KEY.strip()
    if len(key) >= 6:
        redacted = redacted.replace(key, "[redacted]")
    for name, value in os.environ.items():
        upper = name.upper()
        if len(value) < 12:
            continue
        if not any(part in upper for part in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            continue
        redacted = redacted.replace(value, "[redacted]")
    redacted = _SK.sub("[redacted]", redacted)
    redacted = _BEARER.sub("Bearer [redacted]", redacted)
    redacted = _URL_PASSWORD.sub(r"\1[redacted]@", redacted)
    if len(redacted) > _MAX_LEN:
        redacted = redacted[:_MAX_LEN] + "...(truncated)"
    return redacted
