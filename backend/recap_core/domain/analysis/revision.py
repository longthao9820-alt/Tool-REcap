"""Analysis and recap revision identity.

`AnalysisConfig` deliberately excludes every recap/generation input. That is the
mechanism behind "analyze once, generate many": a profile change cannot alter the
analysis config hash, so it cannot create or invalidate an analysis revision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..errors import ValidationError
from ..identity import hash_text, validate_sha256
from ..time_range import validate_non_empty_text

SCHEMA_VERSION = 1


class RevisionStatus(str, Enum):
    OPEN = "OPEN"
    COMPLETE = "COMPLETE"


@dataclass(frozen=True)
class AnalysisConfig:
    """Every causally relevant analysis-side version and setting."""

    proxy_version: str
    audio_version: str
    shot_version: str
    grouper_version: str
    stt_provider: str
    stt_version: str
    stt_model: str
    stt_language: str
    scene_provider: str
    scene_version: str
    story_provider: str
    story_version: str
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "proxy_version",
            "audio_version",
            "shot_version",
            "grouper_version",
            "stt_provider",
            "stt_version",
            "stt_model",
            "stt_language",
            "scene_provider",
            "scene_version",
            "story_provider",
            "story_version",
        ):
            validate_non_empty_text(getattr(self, name), name)
        if not isinstance(self.schema_version, int) or self.schema_version < 1:
            raise ValidationError("schema_version must be an integer >= 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "proxy_version": self.proxy_version,
            "audio_version": self.audio_version,
            "shot_version": self.shot_version,
            "grouper_version": self.grouper_version,
            "stt_provider": self.stt_provider,
            "stt_version": self.stt_version,
            "stt_model": self.stt_model,
            "stt_language": self.stt_language,
            "scene_provider": self.scene_provider,
            "scene_version": self.scene_version,
            "story_provider": self.story_provider,
            "story_version": self.story_version,
            "schema_version": self.schema_version,
        }

    def config_hash(self, source_sha256: str) -> str:
        """Identity of one analysis revision: source bytes plus analysis config."""
        payload = self.to_dict()
        return hash_text(
            validate_sha256(source_sha256),
            *(f"{key}={payload[key]}" for key in sorted(payload)),
        )


@dataclass(frozen=True)
class AnalysisRevision:
    id: str
    episode_id: str
    config_hash: str
    source_sha256: str
    status: RevisionStatus = RevisionStatus.OPEN
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_sha256", validate_sha256(self.source_sha256))
        object.__setattr__(self, "config_hash", validate_sha256(self.config_hash))
        if not isinstance(self.status, RevisionStatus):
            raise ValidationError(f"unknown revision status: {self.status!r}")


@dataclass(frozen=True)
class RecapRevision:
    id: str
    analysis_revision_id: str
    profile_hash: str
    status: RevisionStatus = RevisionStatus.OPEN

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_hash", validate_sha256(self.profile_hash))
        if not isinstance(self.status, RevisionStatus):
            raise ValidationError(f"unknown revision status: {self.status!r}")
