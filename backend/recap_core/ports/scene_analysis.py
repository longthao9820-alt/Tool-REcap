"""Scene-analysis port.

A provider may only label a scene. It cannot alter authoritative time ranges or
source IDs, so the DTO carries no time or shot fields at all: the caller keeps the
locally established geometry and merges only labels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..domain.errors import ProviderOutputInvalidError, ValidationError
from ..domain.time_range import validate_confidence
from ..domain.scene.scene import ScenePackage


@dataclass(frozen=True)
class SceneLabelDTO:
    """Model-supplied labels for exactly one ScenePackage."""

    scene_ordinal: int
    location: str
    summary: str
    confidence: float
    participants: tuple[str, ...] = field(default_factory=tuple)
    objects: tuple[str, ...] = field(default_factory=tuple)


def parse_scene_label_dto(payload: object, expected_ordinal: int) -> SceneLabelDTO:
    """Validate raw provider output into a SceneLabelDTO or fail typed."""
    if not isinstance(payload, dict):
        raise ProviderOutputInvalidError(f"scene label must be an object, got {payload!r}")
    forbidden = {"start_ms", "end_ms", "shot_ids", "id", "analysis_revision_id"}
    intersect = forbidden & set(payload)
    if intersect:
        raise ProviderOutputInvalidError(
            f"scene label may not set authoritative fields: {sorted(intersect)}"
        )
    ordinal = payload.get("scene_ordinal")
    if ordinal != expected_ordinal:
        raise ProviderOutputInvalidError(
            f"scene label is for ordinal {ordinal!r}, expected {expected_ordinal}"
        )
    for key in ("location", "summary"):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ProviderOutputInvalidError(f"scene label {key} must be non-empty text")
    try:
        confidence = validate_confidence(payload.get("confidence", 0.0))
    except ValidationError as exc:
        raise ProviderOutputInvalidError(f"scene label: {exc}") from exc
    participants = payload.get("participants") or []
    objects = payload.get("objects") or []
    for name, value in (("participants", participants), ("objects", objects)):
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ProviderOutputInvalidError(f"scene label {name} must be a list of names")
    return SceneLabelDTO(
        scene_ordinal=expected_ordinal,
        location=payload["location"],
        summary=payload["summary"],
        confidence=confidence,
        participants=tuple(participants),
        objects=tuple(objects),
    )


def ensure_scene_label(value: object, expected_ordinal: int) -> SceneLabelDTO:
    """Re-validate a provider label at the boundary, DTO instance or raw payload."""
    if isinstance(value, SceneLabelDTO):
        payload = {
            "scene_ordinal": value.scene_ordinal,
            "location": value.location,
            "summary": value.summary,
            "confidence": value.confidence,
            "participants": list(value.participants),
            "objects": list(value.objects),
        }
        return parse_scene_label_dto(payload, expected_ordinal)
    return parse_scene_label_dto(value, expected_ordinal)


class SceneAnalysisProvider(Protocol):
    name: str

    @property
    def version(self) -> str: ...

    @property
    def model(self) -> str: ...

    def analyze(self, package: ScenePackage) -> SceneLabelDTO: ...
