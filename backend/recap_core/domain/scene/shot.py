"""Shots and keyframes: camera-cut intervals, never semantic scenes by themselves."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..errors import ValidationError
from ..identity import validate_sha256
from ..time_range import TimeRange, validate_ms, validate_non_empty_text

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Keyframe:
    """One extracted frame, timestamped against the original source timeline."""

    timestamp_ms: int
    path: str
    sha256: str
    size: int
    kind: str = "REPRESENTATIVE"

    def __post_init__(self) -> None:
        validate_ms(self.timestamp_ms, "keyframe timestamp_ms")
        validate_non_empty_text(self.path, "keyframe path")
        object.__setattr__(self, "sha256", validate_sha256(self.sha256))
        if not isinstance(self.size, int) or self.size <= 0:
            raise ValidationError("keyframe size must be a positive integer")
        if self.kind not in ("REPRESENTATIVE", "ACTION"):
            raise ValidationError(f"unknown keyframe kind: {self.kind!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_ms": self.timestamp_ms,
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, data: object) -> "Keyframe":
        if not isinstance(data, dict):
            raise ValidationError(f"keyframe must be an object, got {data!r}")
        try:
            return cls(
                timestamp_ms=data["timestamp_ms"],
                path=data["path"],
                sha256=data["sha256"],
                size=data["size"],
                kind=data.get("kind", "REPRESENTATIVE"),
            )
        except KeyError as exc:
            raise ValidationError(f"keyframe is missing {exc}") from exc


@dataclass(frozen=True)
class Shot:
    """A detected camera-cut interval with at least one source-timestamped frame."""

    id: str
    ordinal: int
    range: TimeRange
    keyframes: tuple[Keyframe, ...] = field(default_factory=tuple)
    excluded_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool) or self.ordinal < 0:
            raise ValidationError("shot ordinal must be an integer >= 0")
        if not self.keyframes:
            raise ValidationError("shot must carry at least one keyframe")
        for keyframe in self.keyframes:
            if not self.range.start_ms <= keyframe.timestamp_ms < self.range.end_ms:
                raise ValidationError(
                    "keyframe {0}ms is outside shot {1}..{2}".format(
                        keyframe.timestamp_ms, self.range.start_ms, self.range.end_ms
                    )
                )
        if self.excluded_reason is not None:
            validate_non_empty_text(self.excluded_reason, "excluded_reason")

    @property
    def is_excluded(self) -> bool:
        return self.excluded_reason is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ordinal": self.ordinal,
            **self.range.to_dict(),
            "keyframes": [keyframe.to_dict() for keyframe in self.keyframes],
            "excluded_reason": self.excluded_reason,
        }
