"""Normalized media metadata. Probe-implementation shapes never reach here."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..errors import ValidationError

SCHEMA_VERSION = 1


def _finite_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field_name} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValidationError(f"{field_name} must be finite, got {value!r}")
    return number


def duration_ms_from_seconds(value: Any) -> int:
    """Convert probe seconds into validated positive integer milliseconds."""
    seconds = _finite_number(value, "duration")
    if seconds <= 0:
        raise ValidationError(f"duration must be positive, got {value!r}")
    milliseconds = int(round(seconds * 1000))
    if milliseconds <= 0:
        raise ValidationError(f"duration rounds to non-positive ms: {value!r}")
    return milliseconds


@dataclass(frozen=True)
class VideoStream:
    index: int
    codec: str
    width: int
    height: int
    rotation: int = 0
    avg_frame_rate: float | None = None

    def __post_init__(self) -> None:
        for name in ("width", "height"):
            value = getattr(self, name)
            if not isinstance(value, int) or value <= 0:
                raise ValidationError(f"video {name} must be a positive integer")
        if self.avg_frame_rate is not None:
            rate = _finite_number(self.avg_frame_rate, "avg_frame_rate")
            if rate <= 0:
                raise ValidationError("avg_frame_rate must be positive")


@dataclass(frozen=True)
class AudioStream:
    index: int
    codec: str
    sample_rate: int
    channels: int

    def __post_init__(self) -> None:
        for name in ("sample_rate", "channels"):
            value = getattr(self, name)
            if not isinstance(value, int) or value <= 0:
                raise ValidationError(f"audio {name} must be a positive integer")


@dataclass(frozen=True)
class MediaMetadata:
    """Validated, probe-agnostic description of one media container."""

    duration_ms: int
    container: str
    video_streams: tuple[VideoStream, ...] = field(default_factory=tuple)
    audio_streams: tuple[AudioStream, ...] = field(default_factory=tuple)
    prober_version: str = "unknown"
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.duration_ms, int) or isinstance(self.duration_ms, bool):
            raise ValidationError("duration_ms must be an integer")
        if self.duration_ms <= 0:
            raise ValidationError("duration_ms must be positive")
        if not self.video_streams and not self.audio_streams:
            raise ValidationError("media must contain at least one decodable stream")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "duration_ms": self.duration_ms,
            "container": self.container,
            "prober_version": self.prober_version,
            "video_streams": [vars(stream) for stream in self.video_streams],
            "audio_streams": [vars(stream) for stream in self.audio_streams],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MediaMetadata":
        try:
            return cls(
                duration_ms=data["duration_ms"],
                container=data["container"],
                video_streams=tuple(VideoStream(**s) for s in data.get("video_streams", [])),
                audio_streams=tuple(AudioStream(**s) for s in data.get("audio_streams", [])),
                prober_version=data.get("prober_version", "unknown"),
                schema_version=data.get("schema_version", SCHEMA_VERSION),
            )
        except (KeyError, TypeError) as exc:
            raise ValidationError(f"malformed media metadata: {exc}") from exc
