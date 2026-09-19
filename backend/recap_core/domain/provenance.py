"""Provenance: the causal identity every derived artifact is bound to."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import ValidationError
from .identity import hash_text, validate_sha256
from .time_range import validate_non_empty_text


@dataclass(frozen=True)
class Provenance:
    """Immutable record of what produced an output and from which source bytes."""

    source_sha256: str
    producer: str
    producer_version: str
    schema_version: int = 1
    prompt_id: str | None = None
    prompt_version: str | None = None
    model: str | None = None
    provider: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_sha256", validate_sha256(self.source_sha256))
        validate_non_empty_text(self.producer, "producer")
        validate_non_empty_text(self.producer_version, "producer_version")
        if not isinstance(self.schema_version, int) or self.schema_version < 1:
            raise ValidationError("schema_version must be an integer >= 1")
        for name in ("prompt_id", "prompt_version", "model", "provider"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValidationError(f"{name} must be text or null")

    def cache_key_parts(self) -> tuple[str, ...]:
        """Ordered identity parts folded into a stage cache key."""
        return (
            self.source_sha256,
            self.producer,
            self.producer_version,
            str(self.schema_version),
            self.prompt_id or "-",
            self.prompt_version or "-",
            self.model or "-",
            self.provider or "-",
        )

    def cache_key(self, *extra: str) -> str:
        return hash_text(*self.cache_key_parts(), *extra)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_sha256": self.source_sha256,
            "producer": self.producer,
            "producer_version": self.producer_version,
            "schema_version": self.schema_version,
            "prompt_id": self.prompt_id,
            "prompt_version": self.prompt_version,
            "model": self.model,
            "provider": self.provider,
        }

    @classmethod
    def from_dict(cls, data: object) -> "Provenance":
        if not isinstance(data, dict):
            raise ValidationError(f"provenance must be an object, got {data!r}")
        known = {
            "source_sha256",
            "producer",
            "producer_version",
            "schema_version",
            "prompt_id",
            "prompt_version",
            "model",
            "provider",
        }
        unknown = set(data) - known
        if unknown:
            raise ValidationError(f"unknown provenance fields: {sorted(unknown)}")
        try:
            return cls(
                source_sha256=data["source_sha256"],
                producer=data["producer"],
                producer_version=data["producer_version"],
                schema_version=data.get("schema_version", 1),
                prompt_id=data.get("prompt_id"),
                prompt_version=data.get("prompt_version"),
                model=data.get("model"),
                provider=data.get("provider"),
            )
        except KeyError as exc:
            raise ValidationError(f"provenance is missing {exc}") from exc
