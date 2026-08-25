"""Project and Episode identities."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ValidationError
from .identity import validate_sha256
from .media.metadata import MediaMetadata


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    root_path: str
    status: str = "NEW"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValidationError("project name must not be empty")
        if not self.root_path.strip():
            raise ValidationError("project root_path must not be empty")


@dataclass(frozen=True)
class Episode:
    """Immutable source identity bound to source bytes and probed metadata."""

    id: str
    project_id: str
    ordinal: int
    source_path: str
    source_sha256: str
    media: MediaMetadata

    def __post_init__(self) -> None:
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool):
            raise ValidationError("episode ordinal must be an integer")
        if self.ordinal < 1:
            raise ValidationError("episode ordinal must be >= 1")
        object.__setattr__(self, "source_sha256", validate_sha256(self.source_sha256))

    @property
    def duration_ms(self) -> int:
        return self.media.duration_ms
