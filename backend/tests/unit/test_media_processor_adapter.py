"""FFmpeg adapter: bounded commands, atomic promotion and typed failure."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from recap_core.domain.errors import (
    MediaProcessingError,
    ProbeUnavailableError,
    SourceMissingError,
)
from recap_core.domain.media.metadata import MediaMetadata, VideoStream
from recap_core.infrastructure.ffmpeg.media_processor import (
    MAX_PROXY_HEIGHT,
    FFmpegMediaProcessor,
    proxy_dimensions,
)
from tests.support import StubCommandRunner, stub_prober


def media(width: int, height: int, rotation: int = 0) -> MediaMetadata:
    return MediaMetadata(
        duration_ms=1000,
        container="mp4",
        video_streams=(
            VideoStream(index=0, codec="h264", width=width, height=height, rotation=rotation),
        ),
    )


def test_proxy_never_upscales_and_preserves_aspect_ratio():
    assert proxy_dimensions(media(320, 240)) == (320, 240)
    assert proxy_dimensions(media(1920, 1080)) == (1280, MAX_PROXY_HEIGHT)
    assert proxy_dimensions(media(1080, 1920)) == (404, MAX_PROXY_HEIGHT)


def test_proxy_dimensions_account_for_rotation():
    """A rotated landscape stream is displayed portrait, so the cap applies to 1920."""
    assert proxy_dimensions(media(1920, 1080, rotation=90)) == (404, MAX_PROXY_HEIGHT)


def test_proxy_dimensions_are_even_for_yuv420p():
    width, height = proxy_dimensions(media(1919, 1081))
    assert width % 2 == 0 and height % 2 == 0


def test_proxy_requires_a_video_stream():
    audio_only = MediaMetadata(
        duration_ms=1000,
        container="mp4",
        audio_streams=(
            __import__(
                "recap_core.domain.media.metadata", fromlist=["AudioStream"]
            ).AudioStream(index=0, codec="aac", sample_rate=16000, channels=1),
        ),
    )
    with pytest.raises(MediaProcessingError):
        proxy_dimensions(audio_only)


def processor(runner: StubCommandRunner) -> FFmpegMediaProcessor:
    return FFmpegMediaProcessor(runner, stub_prober())


def test_missing_binary_is_reported_as_unavailable_capability(tmp_path, source_file):
    runner = StubCommandRunner(raises=FileNotFoundError("ffmpeg"))
    with pytest.raises(ProbeUnavailableError):
        processor(runner).assert_available()
    with pytest.raises(ProbeUnavailableError):
        processor(runner).make_proxy(source_file, tmp_path / "p.mp4", media(320, 240))


def test_timeout_is_reported_as_a_media_processing_failure(tmp_path, source_file):
    runner = StubCommandRunner(raises=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1))
    with pytest.raises(MediaProcessingError):
        processor(runner).make_proxy(source_file, tmp_path / "p.mp4", media(320, 240))


def test_non_zero_exit_keeps_stderr_and_leaves_no_partial_file(tmp_path, source_file):
    runner = StubCommandRunner(exit_code=1, stderr="Invalid data found")
    destination = tmp_path / "out" / "p.mp4"
    with pytest.raises(MediaProcessingError) as failure:
        processor(runner).make_proxy(source_file, destination, media(320, 240))
    assert "Invalid data found" in str(failure.value)
    assert not destination.exists()
    assert not list(destination.parent.glob("*.partial"))


def test_missing_input_fails_before_any_command_runs(tmp_path):
    runner = StubCommandRunner()
    with pytest.raises(SourceMissingError):
        processor(runner).make_stt_audio(tmp_path / "absent.mp4", tmp_path / "a.wav")
    assert runner.calls == []


def test_commands_are_bounded_and_never_write_the_source(tmp_path, source_file):
    class RecordingRunner(StubCommandRunner):
        def __init__(self):
            super().__init__()
            self.timeouts: list[float] = []

        def run(self, argv, timeout_s):
            self.timeouts.append(timeout_s)
            Path(argv[-1]).write_bytes(b"generated")
            return super().run(argv, timeout_s)

    runner = RecordingRunner()
    before = source_file.read_bytes()
    processor(runner).make_proxy(source_file, tmp_path / "p.mp4", media(320, 240))
    assert all(timeout > 0 for timeout in runner.timeouts)
    assert source_file.read_bytes() == before


def test_detect_shots_requires_a_positive_duration(tmp_path, source_file):
    with pytest.raises(MediaProcessingError):
        processor(StubCommandRunner()).detect_shots(source_file, 0, tmp_path, "p")
