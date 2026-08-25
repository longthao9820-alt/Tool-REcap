"""Identity and hash primitives shared by all domain models."""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path

from .errors import ValidationError

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HASH_CHUNK_BYTES = 1024 * 1024


def new_id() -> str:
    """Return a fresh UUID4 identifier."""
    return str(uuid.uuid4())


def validate_sha256(value: str) -> str:
    """Return a normalized lowercase SHA-256 hex digest or fail closed."""
    if not isinstance(value, str):
        raise ValidationError("sha256 must be a string")
    normalized = value.strip().lower()
    if not _SHA256_RE.match(normalized):
        raise ValidationError(f"invalid sha256 digest: {value!r}")
    return normalized


def hash_file(path: Path) -> str:
    """Stream a file and return its SHA-256 digest. The file is only read."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def hash_text(*parts: str) -> str:
    """Return a stable digest over ordered text parts."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def hash_bytes(payload: bytes) -> str:
    """Return the SHA-256 digest of an in-memory payload."""
    return hashlib.sha256(payload).hexdigest()
