"""Transcript domain model. Provider shapes never reach this module."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..errors import ValidationError
from ..provenance import Provenance
from ..time_range import TimeRange, validate_confidence, validate_non_empty_text

SCHEMA_VERSION = 1
UNKNOWN_SPEAKER = "SPEAKER_UNKNOWN"


@dataclass(frozen=True)
class Word:
    """Optional word-level timing inside a segment."""

    text: str
    range: TimeRange

    def __post_init__(self) -> None:
        validate_non_empty_text(self.text, "word text")

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, **self.range.to_dict()}


@dataclass(frozen=True)
class TranscriptSegment:
    """One timestamped speech interval bound to the STT audio it came from."""

    id: str
    ordinal: int
    range: TimeRange
    text: str
    speaker_id: str = UNKNOWN_SPEAKER
    confidence: float = 1.0
    words: tuple[Word, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool) or self.ordinal < 0:
            raise ValidationError("transcript segment ordinal must be an integer >= 0")
        validate_non_empty_text(self.text, "transcript segment text")
        validate_non_empty_text(self.speaker_id, "speaker_id")
        object.__setattr__(self, "confidence", validate_confidence(self.confidence))
        for word in self.words:
            word.range.assert_within(self.range, f"word {word.text!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ordinal": self.ordinal,
            **self.range.to_dict(),
            "speaker_id": self.speaker_id,
            "text": self.text,
            "confidence": self.confidence,
            "words": [word.to_dict() for word in self.words],
        }


@dataclass(frozen=True)
class Transcript:
    """Ordered, non-overlapping segments inside the episode duration."""

    language: str
    duration_ms: int
    audio_sha256: str
    provenance: Provenance
    segments: tuple[TranscriptSegment, ...] = field(default_factory=tuple)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_non_empty_text(self.language, "language")
        if not isinstance(self.duration_ms, int) or self.duration_ms <= 0:
            raise ValidationError("transcript duration_ms must be a positive integer")
        if not self.segments:
            raise ValidationError("transcript must contain at least one segment")
        previous_end = -1
        for index, segment in enumerate(self.segments):
            if segment.ordinal != index:
                raise ValidationError(
                    f"transcript segments must be ordinal-ordered from 0, got {segment.ordinal}"
                )
            if segment.range.start_ms < previous_end:
                raise ValidationError(
                    f"transcript segment {index} overlaps its predecessor"
                )
            segment.range.assert_within_duration(self.duration_ms, f"transcript segment {index}")
            previous_end = segment.range.end_ms

    def slice(self, window: TimeRange) -> tuple[TranscriptSegment, ...]:
        """Segments overlapping `window`, used to build ScenePackages."""
        return tuple(s for s in self.segments if s.range.overlaps(window))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "language": self.language,
            "duration_ms": self.duration_ms,
            "audio_sha256": self.audio_sha256,
            "provenance": self.provenance.to_dict(),
            "segments": [segment.to_dict() for segment in self.segments],
        }
