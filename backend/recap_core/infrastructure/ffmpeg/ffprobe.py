"""FFprobe adapter: raw ffprobe JSON in, validated domain metadata out."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ...domain.errors import (
    ProbeFailedError,
    ProbeUnavailableError,
    SourceMissingError,
    ValidationError,
)
from ...domain.media.metadata import (
    AudioStream,
    MediaMetadata,
    VideoStream,
    duration_ms_from_seconds,
)
from ...ports.command_runner import CommandRunner

DEFAULT_TIMEOUT_S = 60.0


def _rotation(stream: dict[str, Any]) -> int:
    for side_data in stream.get("side_data_list") or []:
        if "rotation" in side_data:
            try:
                return int(float(side_data["rotation"])) % 360
            except (TypeError, ValueError):
                raise ProbeFailedError("invalid rotation side data")
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        try:
            return int(float(tags["rotate"])) % 360
        except (TypeError, ValueError):
            raise ProbeFailedError("invalid rotate tag")
    return 0


def _frame_rate(value: Any) -> float | None:
    if not isinstance(value, str) or "/" not in value:
        return None
    numerator, _, denominator = value.partition("/")
    try:
        num, den = float(numerator), float(denominator)
    except ValueError:
        raise ProbeFailedError(f"invalid frame rate: {value!r}")
    if den == 0:
        return None
    rate = num / den
    return rate if rate > 0 else None


class FFprobeMediaProber:
    """MediaProber implementation over the `ffprobe` binary."""

    def __init__(
        self,
        runner: CommandRunner,
        binary: str = "ffprobe",
        timeout_s: float = DEFAULT_TIMEOUT_S,
        version: str = "ffprobe-1",
    ) -> None:
        self._runner = runner
        self._binary = binary
        self._timeout_s = timeout_s
        self._version = version

    @property
    def version(self) -> str:
        return self._version

    def probe(self, path: Path) -> MediaMetadata:
        source = Path(path)
        if not source.is_file():
            raise SourceMissingError(f"media source not found: {source}")
        argv = [
            self._binary,
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(source),
        ]
        try:
            result = self._runner.run(argv, timeout_s=self._timeout_s)
        except FileNotFoundError as exc:
            raise ProbeUnavailableError(f"ffprobe binary unavailable: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProbeUnavailableError(f"ffprobe timed out after {self._timeout_s}s") from exc
        except OSError as exc:
            raise ProbeUnavailableError(f"ffprobe could not be executed: {exc}") from exc

        if result.exit_code != 0:
            raise ProbeFailedError(
                f"ffprobe exit {result.exit_code}: {result.stderr.strip()[:500]}"
            )
        return self._normalize(result.stdout)

    def _normalize(self, raw_stdout: str) -> MediaMetadata:
        try:
            payload = json.loads(raw_stdout)
        except json.JSONDecodeError as exc:
            raise ProbeFailedError(f"ffprobe output is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ProbeFailedError("ffprobe output is not a JSON object")

        container = payload.get("format") or {}
        if "duration" not in container:
            raise ProbeFailedError("ffprobe output has no format duration")
        raw_duration = container["duration"]
        if isinstance(raw_duration, str):
            try:
                raw_duration = float(raw_duration)
            except ValueError as exc:
                raise ProbeFailedError(f"invalid duration {container['duration']!r}") from exc
        try:
            duration_ms = duration_ms_from_seconds(raw_duration)
        except ValidationError as exc:
            raise ProbeFailedError(str(exc)) from exc

        video: list[VideoStream] = []
        audio: list[AudioStream] = []
        for stream in payload.get("streams") or []:
            if not isinstance(stream, dict):
                raise ProbeFailedError("ffprobe stream entry is not an object")
            kind = stream.get("codec_type")
            try:
                if kind == "video":
                    video.append(
                        VideoStream(
                            index=int(stream["index"]),
                            codec=str(stream.get("codec_name", "unknown")),
                            width=int(stream["width"]),
                            height=int(stream["height"]),
                            rotation=_rotation(stream),
                            avg_frame_rate=_frame_rate(stream.get("avg_frame_rate")),
                        )
                    )
                elif kind == "audio":
                    audio.append(
                        AudioStream(
                            index=int(stream["index"]),
                            codec=str(stream.get("codec_name", "unknown")),
                            sample_rate=int(stream["sample_rate"]),
                            channels=int(stream["channels"]),
                        )
                    )
            except (KeyError, TypeError, ValueError) as exc:
                raise ProbeFailedError(f"malformed ffprobe stream: {exc}") from exc
            except ValidationError as exc:
                raise ProbeFailedError(f"invalid ffprobe stream values: {exc}") from exc

        try:
            return MediaMetadata(
                duration_ms=duration_ms,
                container=str(container.get("format_name", "unknown")),
                video_streams=tuple(video),
                audio_streams=tuple(audio),
                prober_version=self._version,
            )
        except ValidationError as exc:
            raise ProbeFailedError(str(exc)) from exc
