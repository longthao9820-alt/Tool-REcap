"""Port for normalized media probing. Implementations live in infrastructure."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..domain.media.metadata import MediaMetadata


class MediaProber(Protocol):
    """Return validated metadata or raise a typed domain failure."""

    @property
    def version(self) -> str: ...

    def probe(self, path: Path) -> MediaMetadata: ...
