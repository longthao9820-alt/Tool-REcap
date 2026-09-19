"""Characters, atomic events and their authoritative evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..errors import ValidationError
from ..time_range import TimeRange, validate_confidence, validate_non_empty_text

SCHEMA_VERSION = 1


class CharacterStatus(str, Enum):
    RESOLVED = "RESOLVED"
    UNKNOWN = "UNKNOWN"


class EvidenceType(str, Enum):
    TRANSCRIPT = "TRANSCRIPT"
    VISUAL = "VISUAL"
    TIME_RANGE = "TIME_RANGE"


@dataclass(frozen=True)
class Character:
    """A resolved identity or an explicit UNKNOWN placeholder; never a guess."""

    id: str
    analysis_revision_id: str
    canonical_name: str
    status: CharacterStatus = CharacterStatus.UNKNOWN
    aliases: tuple[str, ...] = field(default_factory=tuple)
    refs: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        validate_non_empty_text(self.canonical_name, "canonical_name")
        if not isinstance(self.status, CharacterStatus):
            raise ValidationError(f"unknown character status: {self.status!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "analysis_revision_id": self.analysis_revision_id,
            "canonical_name": self.canonical_name,
            "status": self.status.value,
            "aliases": list(self.aliases),
            "refs": list(self.refs),
        }


@dataclass(frozen=True)
class EvidenceItem:
    """Authoritative transcript/visual/time-range support for exactly one event."""

    id: str
    event_id: str
    analysis_revision_id: str
    type: EvidenceType
    range: TimeRange
    confidence: float = 1.0
    transcript_id: str | None = None
    artifact_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.type, EvidenceType):
            raise ValidationError(f"unknown evidence type: {self.type!r}")
        object.__setattr__(self, "confidence", validate_confidence(self.confidence))
        if self.type is EvidenceType.TRANSCRIPT and not self.transcript_id:
            raise ValidationError("transcript evidence requires a transcript segment reference")
        if self.type is EvidenceType.VISUAL and not self.artifact_id:
            raise ValidationError("visual evidence requires a keyframe artifact reference")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "event_id": self.event_id,
            "analysis_revision_id": self.analysis_revision_id,
            "type": self.type.value,
            **self.range.to_dict(),
            "transcript_id": self.transcript_id,
            "artifact_id": self.artifact_id,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class Event:
    """An atomic claim anchored to one scene, one source range and its evidence."""

    id: str
    scene_id: str
    analysis_revision_id: str
    ordinal: int
    range: TimeRange
    action: str
    cause: str
    consequence: str
    importance: float
    confidence: float
    evidence: tuple[EvidenceItem, ...] = field(default_factory=tuple)
    character_ids: tuple[str, ...] = field(default_factory=tuple)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool) or self.ordinal < 0:
            raise ValidationError("event ordinal must be an integer >= 0")
        validate_non_empty_text(self.action, "event action")
        for name in ("cause", "consequence"):
            if not isinstance(getattr(self, name), str):
                raise ValidationError(f"event {name} must be text")
        object.__setattr__(self, "importance", validate_confidence(self.importance, "importance"))
        object.__setattr__(self, "confidence", validate_confidence(self.confidence))
        if not self.evidence:
            raise ValidationError(f"event {self.id} has no source evidence")
        for item in self.evidence:
            if item.event_id != self.id:
                raise ValidationError(
                    f"evidence {item.id} is attached to event {item.event_id}, not {self.id}"
                )
            if item.analysis_revision_id != self.analysis_revision_id:
                raise ValidationError(
                    f"evidence {item.id} belongs to another analysis revision"
                )
            item.range.assert_within(self.range, f"evidence {item.id}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "scene_id": self.scene_id,
            "analysis_revision_id": self.analysis_revision_id,
            "ordinal": self.ordinal,
            **self.range.to_dict(),
            "character_ids": list(self.character_ids),
            "action": self.action,
            "cause": self.cause,
            "consequence": self.consequence,
            "importance": self.importance,
            "confidence": self.confidence,
            "evidence_ids": [item.id for item in self.evidence],
        }
