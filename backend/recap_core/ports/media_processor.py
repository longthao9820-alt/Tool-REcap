"""Port for local media derivation (proxy, STT audio, shots/keyframes)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..domain.media.metadata import MediaMetadata


@dataclass(frozen=True)
class ProxyResult:
    path: Path
    width: int
    height: int
    duration_ms: int


@dataclass(frozen=True)
class AudioResult:
    path: Path
    sample_rate: int
    channels: int
    duration_ms: int


@dataclass(frozen=True)
class KeyframeResult:
    timestamp_ms: int
    path: Path


@dataclass(frozen=True)
class ShotResult:
    """One detected cut interval plus the frames extracted from it."""

    start_ms: int
    end_ms: int
    keyframes: tuple[KeyframeResult, ...] = field(default_factory=tuple)


class MediaProcessor(Protocol):
    """Real local media work. Every method writes atomically or raises typed."""

    @property
    def proxy_version(self) -> str: ...

    @property
    def audio_version(self) -> str: ...

    @property
    def shot_version(self) -> str: ...

    def make_proxy(self, source: Path, destination: Path, media: MediaMetadata) -> ProxyResult: ...

    def make_stt_audio(self, source: Path, destination: Path) -> AudioResult: ...

    def detect_shots(
        self, proxy: Path, duration_ms: int, keyframe_dir: Path, name_prefix: str
    ) -> tuple[ShotResult, ...]: ...
