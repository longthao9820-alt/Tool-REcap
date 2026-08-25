"""FFprobe adapter: normalization plus fail-closed behaviour."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests.support import VALID_FFPROBE_JSON, StubCommandRunner, stub_prober
from recap_core.domain.errors import (
    ProbeFailedError,
    ProbeUnavailableError,
    SourceMissingError,
)
from recap_core.infrastructure.ffmpeg.ffprobe import FFprobeMediaProber
from recap_core.infrastructure.ffmpeg.subprocess_runner import SubprocessCommandRunner


def test_valid_output_is_normalized(source_file):
    media = stub_prober().probe(source_file)
    assert media.duration_ms == 1000
    assert media.container == "mov,mp4,m4a"
    assert media.video_streams[0].width == 320
    assert media.video_streams[0].avg_frame_rate == 10.0
    assert media.audio_streams[0].sample_rate == 16000
    assert media.schema_version == 1


def test_rotation_is_normalized_from_side_data(source_file):
    payload = json.loads(json.dumps(VALID_FFPROBE_JSON))
    payload["streams"][0]["side_data_list"] = [{"rotation": -90}]
    media = stub_prober(payload).probe(source_file)
    assert media.video_streams[0].rotation == 270


def test_missing_source_fails_closed(tmp_path):
    with pytest.raises(SourceMissingError):
        stub_prober().probe(tmp_path / "nope.mp4")


def test_invalid_json_fails_closed(source_file):
    with pytest.raises(ProbeFailedError):
        stub_prober("{not json").probe(source_file)


def test_non_object_json_fails_closed(source_file):
    with pytest.raises(ProbeFailedError):
        stub_prober("[]").probe(source_file)


@pytest.mark.parametrize("duration", ["nan", "inf", "-inf", "0", "-3.0", "abc", None])
def test_non_finite_or_invalid_duration_fails_closed(source_file, duration):
    payload = json.loads(json.dumps(VALID_FFPROBE_JSON))
    if duration is None:
        payload["format"].pop("duration")
    else:
        payload["format"]["duration"] = duration
    with pytest.raises(ProbeFailedError):
        stub_prober(payload).probe(source_file)


def test_no_streams_fails_closed(source_file):
    payload = json.loads(json.dumps(VALID_FFPROBE_JSON))
    payload["streams"] = []
    with pytest.raises(ProbeFailedError):
        stub_prober(payload).probe(source_file)


def test_malformed_stream_fails_closed(source_file):
    payload = json.loads(json.dumps(VALID_FFPROBE_JSON))
    payload["streams"][0]["width"] = "wide"
    with pytest.raises(ProbeFailedError):
        stub_prober(payload).probe(source_file)


def test_negative_dimension_fails_closed(source_file):
    payload = json.loads(json.dumps(VALID_FFPROBE_JSON))
    payload["streams"][0]["height"] = 0
    with pytest.raises(ProbeFailedError):
        stub_prober(payload).probe(source_file)


def test_probe_exit_failure_is_typed(source_file):
    with pytest.raises(ProbeFailedError):
        stub_prober(exit_code=1, stderr="broken file").probe(source_file)


@pytest.mark.parametrize(
    "error",
    [FileNotFoundError("ffprobe"), OSError("denied"), subprocess.TimeoutExpired("ffprobe", 1)],
)
def test_unavailable_backend_is_typed(source_file, error):
    prober = FFprobeMediaProber(StubCommandRunner(raises=error))
    with pytest.raises(ProbeUnavailableError):
        prober.probe(source_file)


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not installed")
def test_real_ffprobe_probes_the_committed_fixture(source_file):
    media = FFprobeMediaProber(SubprocessCommandRunner()).probe(source_file)
    assert 900 <= media.duration_ms <= 1200
    assert media.video_streams and media.audio_streams
