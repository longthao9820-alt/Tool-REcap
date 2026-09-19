"""Scenes and the ScenePackage handed to a scene-analysis provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..errors import ValidationError
from ..provenance import Provenance
from ..time_range import TimeRange, validate_confidence, validate_non_empty_text
from ..transcript.transcript import TranscriptSegment
from .shot import Keyframe, Shot

SCHEMA_VERSION = 1
UNKNOWN_LOCATION = "UNKNOWN"


@dataclass(frozen=True)
class SceneCandidate:
    """Authoritative scene geometry produced by local grouping.

    A provider may label a candidate but can never change its range or shots.
    """

    ordinal: int
    shots: tuple[Shot, ...]

    def __post_init__(self) -> None:
        if not self.shots:
            raise ValidationError("scene candidate must contain at least one shot")
        ordinals = [shot.ordinal for shot in self.shots]
        if ordinals != sorted(ordinals):
            raise ValidationError("scene candidate shots must be ordered")

    @property
    def range(self) -> TimeRange:
        return TimeRange(self.shots[0].range.start_ms, self.shots[-1].range.end_ms)

    @property
    def shot_ids(self) -> tuple[str, ...]:
        return tuple(shot.id for shot in self.shots)

    @property
    def keyframes(self) -> tuple[Keyframe, ...]:
        return tuple(frame for shot in self.shots for frame in shot.keyframes)

    @property
    def excluded_reasons(self) -> tuple[str, ...]:
        return tuple(
            shot.excluded_reason for shot in self.shots if shot.excluded_reason is not None
        )


@dataclass(frozen=True)
class ScenePackage:
    """Exactly the bounded context a scene-analysis provider is allowed to see."""

    scene_ordinal: int
    range: TimeRange
    shot_ids: tuple[str, ...]
    transcript_slice: tuple[TranscriptSegment, ...]
    keyframes: tuple[Keyframe, ...]
    provenance: Provenance
    previous_summary: str | None = None
    next_summary: str | None = None
    excluded_reasons: tuple[str, ...] = field(default_factory=tuple)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.shot_ids:
            raise ValidationError("scene package must reference at least one shot")
        for segment in self.transcript_slice:
            if not segment.range.overlaps(self.range):
                raise ValidationError(
                    f"transcript segment {segment.id} does not overlap the scene range"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scene_ordinal": self.scene_ordinal,
            **self.range.to_dict(),
            "shot_ids": list(self.shot_ids),
            "transcript": [segment.to_dict() for segment in self.transcript_slice],
            "keyframes": [keyframe.to_dict() for keyframe in self.keyframes],
            "previous_summary": self.previous_summary,
            "next_summary": self.next_summary,
            "excluded_reasons": list(self.excluded_reasons),
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True)
class Scene:
    """A coherent story interval composed of ordered shots."""

    id: str
    analysis_revision_id: str
    ordinal: int
    range: TimeRange
    shot_ids: tuple[str, ...]
    location: str
    summary: str
    confidence: float
    provenance: Provenance
    character_ids: tuple[str, ...] = field(default_factory=tuple)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool) or self.ordinal < 0:
            raise ValidationError("scene ordinal must be an integer >= 0")
        if not self.shot_ids:
            raise ValidationError("scene must reference at least one shot")
        if len(set(self.shot_ids)) != len(self.shot_ids):
            raise ValidationError("scene shot references must be unique")
        validate_non_empty_text(self.location, "scene location")
        validate_non_empty_text(self.summary, "scene summary")
        object.__setattr__(self, "confidence", validate_confidence(self.confidence))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "analysis_revision_id": self.analysis_revision_id,
            "ordinal": self.ordinal,
            **self.range.to_dict(),
            "shot_ids": list(self.shot_ids),
            "character_ids": list(self.character_ids),
            "location": self.location,
            "summary": self.summary,
            "confidence": self.confidence,
            "provenance": self.provenance.to_dict(),
        }
