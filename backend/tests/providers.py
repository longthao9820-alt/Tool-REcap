"""Deterministic test doubles for the AI/STT boundaries.

These adapters exist only for UNIT execution. They deliberately live in the test
tree: the production composition root authorizes providers by registered name and
by defining module, so nothing here can be selected by production configuration.
They make no quality claim about any real model.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any, Sequence

from recap_core.domain.media.metadata import MediaMetadata
from recap_core.domain.scene.scene import ScenePackage
from recap_core.ports.media_processor import (
    AudioResult,
    KeyframeResult,
    ProxyResult,
    ShotResult,
)
from recap_core.ports.scene_analysis import SceneLabelDTO
from recap_core.ports.story_reasoning import (
    EdgeDraftDTO,
    EventDraftDTO,
    EvidenceDraftDTO,
    PlotDraftDTO,
    StoryReasoningDTO,
)
from recap_core.ports.stt import ProviderCapability, TranscriptDTO, TranscriptSegmentDTO
from recap_core.domain.story.graph import EdgeRelation

PARTICIPANT_NAMES = ("An", "Binh", "Chi")


def wav_duration_ms(path: Path) -> int:
    """Real duration of a PCM WAV file, using only the standard library."""
    with wave.open(str(path), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
    if rate <= 0 or frames <= 0:
        raise ValueError(f"unusable wav file: {path}")
    return max(1, int(round(frames * 1000.0 / rate)))


class DeterministicSTTProvider:
    """Splits the real STT audio duration into a fixed number of scripted turns."""

    name = "deterministic-test-stt"

    def __init__(self, segments: int = 3, language: str = "vi") -> None:
        self._segments = max(1, segments)
        self._language = language
        self.calls = 0

    @property
    def version(self) -> str:
        return "deterministic-stt-1"

    @property
    def model(self) -> str:
        return "deterministic-tiny"

    def capability(self) -> ProviderCapability:
        return ProviderCapability(available=True, detail="deterministic", model=self.model)

    def transcribe(
        self, audio_path: Path, language: str, options: dict[str, Any] | None = None
    ) -> TranscriptDTO:
        self.calls += 1
        duration_ms = wav_duration_ms(Path(audio_path))
        count = min(self._segments, max(1, duration_ms // 2))
        step = duration_ms // count
        segments = []
        for index in range(count):
            start = index * step
            end = duration_ms if index == count - 1 else start + step
            text = f"cau thoai {index}"
            segments.append(
                TranscriptSegmentDTO(
                    start_ms=start,
                    end_ms=end,
                    text=text,
                    speaker_id=f"SPEAKER_{index % 2:02d}",
                    confidence=0.9,
                    words=(
                        {"text": text.replace(" ", "-"), "start_ms": start, "end_ms": end},
                    ),
                )
            )
        return TranscriptDTO(
            language=language or self._language,
            segments=tuple(segments),
            model=self.model,
            provider_version=self.version,
        )


class DeterministicSceneAnalysisProvider:
    """Labels a ScenePackage from the local evidence it was given, nothing else."""

    name = "deterministic-test-scene"

    def __init__(self) -> None:
        self.calls = 0
        self.seen_ordinals: list[int] = []
        self.packages: list[ScenePackage] = []

    @property
    def version(self) -> str:
        return "deterministic-scene-1"

    @property
    def model(self) -> str:
        return "deterministic-vision"

    def analyze(self, package: ScenePackage) -> SceneLabelDTO:
        self.calls += 1
        self.seen_ordinals.append(package.scene_ordinal)
        self.packages.append(package)
        spoken = " ".join(segment.text for segment in package.transcript_slice)
        summary = spoken or f"canh {package.scene_ordinal} khong co thoai"
        return SceneLabelDTO(
            scene_ordinal=package.scene_ordinal,
            location=f"BOI_CANH_{package.scene_ordinal}",
            summary=summary,
            confidence=0.8,
            participants=(
                PARTICIPANT_NAMES[package.scene_ordinal % len(PARTICIPANT_NAMES)],
                "UNKNOWN",
            ),
        )


class DeterministicStoryReasoningProvider:
    """Derives events, a variable number of plots and typed edges deterministically."""

    name = "deterministic-test-story"

    def __init__(self, events_per_scene: int = 2, plot_size: int = 2) -> None:
        self._events_per_scene = max(1, events_per_scene)
        self._plot_size = max(1, plot_size)
        self.extract_calls = 0
        self.reason_calls = 0

    @property
    def version(self) -> str:
        return "deterministic-story-1"

    @property
    def model(self) -> str:
        return "deterministic-reasoner"

    def extract_events(self, package: ScenePackage) -> tuple[EventDraftDTO, ...]:
        self.extract_calls += 1
        start, end = package.range.start_ms, package.range.end_ms
        count = min(self._events_per_scene, max(1, (end - start) // 2))
        step = (end - start) // count
        drafts = []
        for index in range(count):
            event_start = start + index * step
            event_end = end if index == count - 1 else event_start + step
            drafts.append(
                EventDraftDTO(
                    start_ms=event_start,
                    end_ms=event_end,
                    action=f"nhan vat hanh dong trong canh {package.scene_ordinal} buoc {index}",
                    cause=f"tiep noi buoc {index - 1}" if index else "mo dau canh",
                    consequence=f"dan toi buoc {index + 1}",
                    importance=0.9 if (package.scene_ordinal == 0 and index == 0) else 0.5,
                    confidence=0.8,
                    participants=(
                        PARTICIPANT_NAMES[
                            (package.scene_ordinal + index) % len(PARTICIPANT_NAMES)
                        ],
                    ),
                    evidence=self._evidence(package, event_start, event_end),
                )
            )
        return tuple(drafts)

    @staticmethod
    def _evidence(package: ScenePackage, start: int, end: int) -> tuple[EvidenceDraftDTO, ...]:
        """Only real, in-range references from the package are ever emitted."""
        items: list[EvidenceDraftDTO] = []
        for segment in package.transcript_slice:
            overlap_start = max(segment.range.start_ms, start)
            overlap_end = min(segment.range.end_ms, end)
            if overlap_end > overlap_start:
                items.append(
                    EvidenceDraftDTO(
                        type="TRANSCRIPT",
                        start_ms=overlap_start,
                        end_ms=overlap_end,
                        confidence=0.9,
                        transcript_ref=segment.id,
                    )
                )
        for keyframe in package.keyframes:
            if start <= keyframe.timestamp_ms < end:
                items.append(
                    EvidenceDraftDTO(
                        type="VISUAL",
                        start_ms=keyframe.timestamp_ms,
                        end_ms=min(keyframe.timestamp_ms + 1, end),
                        confidence=0.7,
                        keyframe_timestamp_ms=keyframe.timestamp_ms,
                    )
                )
        if not items:
            items.append(
                EvidenceDraftDTO(type="TIME_RANGE", start_ms=start, end_ms=end, confidence=0.5)
            )
        return tuple(items)

    def reason_story(
        self, events: Sequence[dict[str, object]], known_event_ids: Sequence[str]
    ) -> StoryReasoningDTO:
        self.reason_calls += 1
        ids = list(known_event_ids)
        plots = []
        for index in range(0, len(ids), self._plot_size):
            chunk = ids[index : index + self._plot_size]
            plots.append(
                PlotDraftDTO(
                    title=f"Tuyen truyen {index // self._plot_size}",
                    summary=f"Chuoi su kien {index // self._plot_size}",
                    importance=0.8,
                    event_ids=tuple(chunk),
                    membership_scores=tuple(1.0 for _ in chunk),
                )
            )
        edges = [
            EdgeDraftDTO(
                from_event_id=ids[index],
                to_event_id=ids[index + 1],
                relation=EdgeRelation.CAUSE,
                confidence=0.8,
            )
            for index in range(len(ids) - 1)
        ]
        if len(ids) >= 3:
            edges.append(
                EdgeDraftDTO(
                    from_event_id=ids[0],
                    to_event_id=ids[-1],
                    relation=EdgeRelation.PAYOFF,
                    confidence=0.7,
                )
            )
        return StoryReasoningDTO(plots=tuple(plots), edges=tuple(edges))


class FakeMediaProcessor:
    """Writes small deterministic files so DAG tests need no FFmpeg.

    It fabricates media bytes, never media quality: acceptance evidence for the
    real proxy/audio/keyframe contract comes from the FFmpeg adapter instead.
    """

    def __init__(self, shots: int = 2, sample_rate: int = 16_000) -> None:
        self._shots = max(1, shots)
        self._sample_rate = sample_rate
        self.proxy_calls = 0
        self.audio_calls = 0
        self.shot_calls = 0
        self.salt = b""

    @property
    def proxy_version(self) -> str:
        return "fake-proxy-1"

    @property
    def audio_version(self) -> str:
        return "fake-audio-1"

    @property
    def shot_version(self) -> str:
        return "fake-shots-1"

    def make_proxy(self, source: Path, destination: Path, media: MediaMetadata) -> ProxyResult:
        self.proxy_calls += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"proxy:" + source.name.encode("utf-8") + self.salt)
        return ProxyResult(
            path=destination, width=320, height=240, duration_ms=media.duration_ms
        )

    def make_stt_audio(self, source: Path, destination: Path) -> AudioResult:
        self.audio_calls += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        frames = self._sample_rate  # exactly one second of silence
        with wave.open(str(destination), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(self._sample_rate)
            handle.writeframes(b"\x00\x00" * frames)
        return AudioResult(
            path=destination, sample_rate=self._sample_rate, channels=1, duration_ms=1000
        )

    def detect_shots(
        self, proxy: Path, duration_ms: int, keyframe_dir: Path, name_prefix: str
    ) -> tuple[ShotResult, ...]:
        self.shot_calls += 1
        keyframe_dir.mkdir(parents=True, exist_ok=True)
        count = min(self._shots, max(1, duration_ms // 2))
        step = duration_ms // count
        results = []
        for index in range(count):
            start = index * step
            end = duration_ms if index == count - 1 else start + step
            timestamp = min(start + (end - start) // 2, end - 1)
            path = keyframe_dir / f"{name_prefix}.shot{index:04d}.{timestamp}.jpg"
            path.write_bytes(f"keyframe:{name_prefix}:{timestamp}".encode("utf-8") + self.salt)
            results.append(
                ShotResult(
                    start_ms=start,
                    end_ms=end,
                    keyframes=(KeyframeResult(timestamp_ms=timestamp, path=path),),
                )
            )
        return tuple(results)
