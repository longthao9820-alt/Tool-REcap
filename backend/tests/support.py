"""Shared test doubles and fixture paths."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from recap_core.infrastructure.ffmpeg.ffprobe import FFprobeMediaProber
from recap_core.ports.command_runner import CommandResult

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_EPISODE = FIXTURES / "sample_episode.mp4"

VALID_FFPROBE_JSON = {
    "format": {"format_name": "mov,mp4,m4a", "duration": "1.000000"},
    "streams": [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "h264",
            "width": 320,
            "height": 240,
            "avg_frame_rate": "10/1",
        },
        {
            "index": 1,
            "codec_type": "audio",
            "codec_name": "aac",
            "sample_rate": "16000",
            "channels": 1,
        },
    ],
}


class StubCommandRunner:
    """Deterministic CommandRunner returning a scripted result or raising."""

    def __init__(self, stdout: str = "", exit_code: int = 0, stderr: str = "", raises=None):
        self.stdout = stdout
        self.exit_code = exit_code
        self.stderr = stderr
        self.raises = raises
        self.calls: list[list[str]] = []

    def run(self, argv: Sequence[str], timeout_s: float) -> CommandResult:
        self.calls.append(list(argv))
        if self.raises is not None:
            raise self.raises
        return CommandResult(exit_code=self.exit_code, stdout=self.stdout, stderr=self.stderr)


def stub_prober(payload: dict | str | None = None, **kwargs) -> FFprobeMediaProber:
    """FFprobe adapter wired to a stubbed backend."""
    if payload is None:
        payload = VALID_FFPROBE_JSON
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    return FFprobeMediaProber(StubCommandRunner(stdout=stdout, **kwargs))
