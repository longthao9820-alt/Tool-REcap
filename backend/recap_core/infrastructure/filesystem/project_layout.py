"""Project directory layout and safe artifact destinations."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ...domain.errors import UnsafeArtifactPathError

PROJECT_DIRECTORIES: tuple[str, ...] = (
    "source",
    "proxy",
    "metadata",
    "transcript",
    "scenes/keyframes",
    "story",
    "recap",
    "voice",
    "timeline",
    "subtitles",
    "previews",
    "renders",
    "qc",
    "cache",
    "logs",
)


class ProjectLayout:
    """Owns every path a project may generate into."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    def create(self) -> "ProjectLayout":
        for relative in PROJECT_DIRECTORIES:
            (self.root / relative).mkdir(parents=True, exist_ok=True)
        return self

    def resolve(self, relative: str | Path) -> Path:
        """Resolve a generated-artifact destination inside the project root."""
        candidate = Path(relative)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (self.root / candidate).resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise UnsafeArtifactPathError(
                f"artifact destination escapes project root: {relative!r}"
            )
        return resolved

    def write_atomic(self, relative: str | Path, payload: bytes) -> Path:
        """Write via `.partial` and promote only after the bytes are durable."""
        target = self.resolve(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=target.name + ".", suffix=".partial"
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return target
