"""Real local FFmpeg adapters: 720p proxy, mono STT audio, shots and keyframes.

Every command is bounded, writes to a unique `.partial` file, is validated by
ffprobe and only then atomically promoted. Nothing here ever writes the source.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from ...domain.errors import (
    MediaProcessingError,
    ProbeUnavailableError,
    SourceMissingError,
    UnsafeArtifactPathError,
)
from ...domain.media.metadata import MediaMetadata
from ...ports.command_runner import CommandRunner
from ...ports.media_processor import AudioResult, KeyframeResult, ProxyResult, ShotResult
from ...ports.media_prober import MediaProber

PROXY_VERSION = "ffmpeg-proxy-720p-1"
AUDIO_VERSION = "ffmpeg-sttaudio-mono16k-1"
SHOT_VERSION = "ffmpeg-shots-scenecut-1"

MAX_PROXY_HEIGHT = 720
STT_SAMPLE_RATE = 16_000
STT_CHANNELS = 1
DEFAULT_TIMEOUT_S = 600.0
SCENE_THRESHOLD = 0.4
MIN_SHOT_MS = 400

_PTS_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


def proxy_dimensions(media: MediaMetadata) -> tuple[int, int]:
    """Target proxy size: at most 720 lines, aspect ratio preserved, never upscaled."""
    if not media.video_streams:
        raise MediaProcessingError("cannot build a video proxy without a video stream")
    stream = media.video_streams[0]
    width, height = stream.width, stream.height
    if stream.rotation % 180 == 90:
        width, height = height, width
    if height <= MAX_PROXY_HEIGHT:
        return _even(width), _even(height)
    scaled_width = int(round(width * MAX_PROXY_HEIGHT / height))
    return _even(scaled_width), MAX_PROXY_HEIGHT


def _even(value: int) -> int:
    """Round down to an even number; H.264 chroma subsampling requires it."""
    if value < 2:
        return 2
    return value - (value % 2)


class FFmpegMediaProcessor:
    """MediaProcessor implementation over the `ffmpeg` binary."""

    def __init__(
        self,
        runner: CommandRunner,
        prober: MediaProber,
        binary: str = "ffmpeg",
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._runner = runner
        self._prober = prober
        self._binary = binary
        self._timeout_s = timeout_s

    @property
    def proxy_version(self) -> str:
        return PROXY_VERSION

    @property
    def audio_version(self) -> str:
        return AUDIO_VERSION

    @property
    def shot_version(self) -> str:
        return SHOT_VERSION

    # -- capability ----------------------------------------------------------

    def assert_available(self) -> str:
        """Fail closed when the local FFmpeg capability is missing."""
        result = self._run(["-hide_banner", "-version"], timeout_s=30.0)
        first_line = result.stdout.splitlines()[0] if result.stdout else ""
        if not first_line:
            raise ProbeUnavailableError("ffmpeg reported no version output")
        return first_line.strip()

    # -- proxy ---------------------------------------------------------------

    def make_proxy(self, source: Path, destination: Path, media: MediaMetadata) -> ProxyResult:
        source = self._require_source(source)
        width, height = proxy_dimensions(media)
        argv = [
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i", str(source),
            "-map", "0:v:0",
            "-vf", f"scale={width}:{height}",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "26",
            "-pix_fmt", "yuv420p",
            "-an",
            "-movflags", "+faststart",
            "-f", "mp4",
        ]
        probed = self._produce(destination, argv, suffix=".mp4")
        if not probed.video_streams:
            raise MediaProcessingError("generated proxy has no decodable video stream")
        stream = probed.video_streams[0]
        if stream.height > MAX_PROXY_HEIGHT:
            raise MediaProcessingError(
                f"generated proxy is {stream.height} lines, above {MAX_PROXY_HEIGHT}"
            )
        return ProxyResult(
            path=destination,
            width=stream.width,
            height=stream.height,
            duration_ms=probed.duration_ms,
        )

    # -- STT audio -----------------------------------------------------------

    def make_stt_audio(self, source: Path, destination: Path) -> AudioResult:
        source = self._require_source(source)
        argv = [
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i", str(source),
            "-map", "0:a:0",
            "-vn",
            "-ac", str(STT_CHANNELS),
            "-ar", str(STT_SAMPLE_RATE),
            "-c:a", "pcm_s16le",
            "-f", "wav",
        ]
        probed = self._produce(destination, argv, suffix=".wav")
        if not probed.audio_streams:
            raise MediaProcessingError("generated STT audio has no decodable audio stream")
        stream = probed.audio_streams[0]
        if stream.channels != STT_CHANNELS or stream.sample_rate != STT_SAMPLE_RATE:
            raise MediaProcessingError(
                "generated STT audio is {0}ch/{1}Hz, expected {2}ch/{3}Hz".format(
                    stream.channels, stream.sample_rate, STT_CHANNELS, STT_SAMPLE_RATE
                )
            )
        return AudioResult(
            path=destination,
            sample_rate=stream.sample_rate,
            channels=stream.channels,
            duration_ms=probed.duration_ms,
        )

    # -- shots and keyframes -------------------------------------------------

    def detect_shots(
        self, proxy: Path, duration_ms: int, keyframe_dir: Path, name_prefix: str
    ) -> tuple[ShotResult, ...]:
        """Detect cuts, then extract one source-timestamped frame per shot.

        A source with no detected cut is one valid shot spanning the episode, not
        an empty result: downstream stages always receive at least one shot.
        """
        proxy = self._require_source(proxy)
        if duration_ms <= 0:
            raise MediaProcessingError("cannot detect shots without a positive duration")
        cuts = self._detect_cut_times_ms(proxy)
        boundaries = [0]
        for cut in cuts:
            if cut - boundaries[-1] >= MIN_SHOT_MS and cut < duration_ms:
                boundaries.append(cut)
        boundaries.append(duration_ms)

        keyframe_dir.mkdir(parents=True, exist_ok=True)
        shots: list[ShotResult] = []
        for index in range(len(boundaries) - 1):
            start_ms, end_ms = boundaries[index], boundaries[index + 1]
            if end_ms <= start_ms:
                continue
            timestamp_ms = min(start_ms + (end_ms - start_ms) // 2, end_ms - 1)
            frame_path = keyframe_dir / f"{name_prefix}.shot{index:04d}.{timestamp_ms}.jpg"
            self._extract_frame(proxy, timestamp_ms, frame_path)
            shots.append(
                ShotResult(
                    start_ms=start_ms,
                    end_ms=end_ms,
                    keyframes=(
                        KeyframeResult(timestamp_ms=timestamp_ms, path=frame_path),
                    ),
                )
            )
        if not shots:
            raise MediaProcessingError("shot detection produced no shots")
        return tuple(shots)

    def _detect_cut_times_ms(self, proxy: Path) -> list[int]:
        result = self._run(
            [
                "-hide_banner",
                "-nostdin",
                "-i", str(proxy),
                "-filter:v", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo",
                "-f", "null",
                "-",
            ],
            timeout_s=self._timeout_s,
            allow_failure=False,
        )
        times: list[int] = []
        for match in _PTS_RE.finditer(result.stderr):
            milliseconds = int(round(float(match.group(1)) * 1000))
            if milliseconds > 0:
                times.append(milliseconds)
        return sorted(set(times))

    def _extract_frame(self, proxy: Path, timestamp_ms: int, destination: Path) -> None:
        argv = [
            "-hide_banner",
            "-nostdin",
            "-y",
            "-ss", f"{timestamp_ms / 1000.0:.3f}",
            "-i", str(proxy),
            "-frames:v", "1",
            "-q:v", "3",
            "-f", "image2",
        ]
        temporary = self._staging_path(destination, ".jpg")
        try:
            self._run(argv + [str(temporary)], timeout_s=self._timeout_s)
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise MediaProcessingError(
                    f"keyframe extraction at {timestamp_ms}ms produced no output"
                )
            os.replace(temporary, destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    # -- internals -----------------------------------------------------------

    def _require_source(self, path: Path) -> Path:
        resolved = Path(path)
        if not resolved.is_file():
            raise SourceMissingError(f"media input not found: {resolved}")
        return resolved

    @staticmethod
    def _staging_path(destination: Path, suffix: str) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(
            dir=str(destination.parent), prefix=destination.name + ".", suffix=suffix + ".partial"
        )
        os.close(handle)
        staging = Path(name)
        staging.unlink(missing_ok=True)
        return staging

    def _produce(self, destination: Path, argv: list[str], *, suffix: str) -> MediaMetadata:
        """Run one bounded command into staging, validate it, then promote it."""
        destination = Path(destination)
        if destination.exists() and not destination.is_file():
            raise UnsafeArtifactPathError(f"artifact destination is not a file: {destination}")
        staging = self._staging_path(destination, suffix)
        try:
            self._run(argv + [str(staging)], timeout_s=self._timeout_s)
            if not staging.is_file() or staging.stat().st_size == 0:
                raise MediaProcessingError(f"ffmpeg produced no output for {destination.name}")
            probed = self._prober.probe(staging)
            os.replace(staging, destination)
            return probed
        except BaseException:
            staging.unlink(missing_ok=True)
            raise

    def _run(self, argv: list[str], *, timeout_s: float, allow_failure: bool = False):
        command = [self._binary, *argv]
        try:
            result = self._runner.run(command, timeout_s=timeout_s)
        except FileNotFoundError as exc:
            raise ProbeUnavailableError(f"ffmpeg binary unavailable: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise MediaProcessingError(f"ffmpeg timed out after {timeout_s}s") from exc
        except OSError as exc:
            raise ProbeUnavailableError(f"ffmpeg could not be executed: {exc}") from exc
        if result.exit_code != 0 and not allow_failure:
            raise MediaProcessingError(
                # ffmpeg reports the actual cause on its last stderr lines.
                "ffmpeg exit {0}: {1}".format(result.exit_code, result.stderr.strip()[-600:])
            )
        return result
