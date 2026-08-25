"""Domain invariants: identity, metadata validation and job transitions."""

from __future__ import annotations

import math

import pytest

from recap_core.domain.errors import ValidationError
from recap_core.domain.identity import hash_file, new_id, validate_sha256
from recap_core.domain.jobs.job import (
    Job,
    JobState,
    assert_transition,
    can_transition,
    job_input_hash,
)
from recap_core.domain.media.metadata import (
    AudioStream,
    MediaMetadata,
    VideoStream,
    duration_ms_from_seconds,
)
from recap_core.domain.project import Episode, Project


def media(**overrides) -> MediaMetadata:
    defaults = dict(
        duration_ms=1000,
        container="mov,mp4,m4a",
        video_streams=(VideoStream(index=0, codec="h264", width=320, height=240),),
        audio_streams=(AudioStream(index=1, codec="aac", sample_rate=16000, channels=1),),
    )
    defaults.update(overrides)
    return MediaMetadata(**defaults)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, 0, -1.5, "1.0", True, None])
def test_duration_conversion_fails_closed(value):
    with pytest.raises(ValidationError):
        duration_ms_from_seconds(value)


def test_duration_conversion_rounds_to_milliseconds():
    assert duration_ms_from_seconds(1.2345) == 1234
    assert duration_ms_from_seconds(0.0006) == 1


def test_metadata_requires_a_decodable_stream():
    with pytest.raises(ValidationError):
        media(video_streams=(), audio_streams=())


@pytest.mark.parametrize("duration", [0, -1, 1.5, True])
def test_metadata_duration_must_be_positive_integer(duration):
    with pytest.raises(ValidationError):
        media(duration_ms=duration)


def test_metadata_round_trip():
    original = media()
    assert MediaMetadata.from_dict(original.to_dict()) == original


def test_metadata_from_malformed_dict_fails_closed():
    with pytest.raises(ValidationError):
        MediaMetadata.from_dict({"container": "mp4"})


def test_video_stream_dimensions_must_be_positive():
    with pytest.raises(ValidationError):
        VideoStream(index=0, codec="h264", width=0, height=240)
    with pytest.raises(ValidationError):
        VideoStream(index=0, codec="h264", width=320, height=240, avg_frame_rate=math.inf)


def test_validate_sha256_normalizes_and_rejects():
    assert validate_sha256("A" * 64) == "a" * 64
    for bad in ["", "zz", "g" * 64, 123]:
        with pytest.raises(ValidationError):
            validate_sha256(bad)


def test_hash_file_matches_known_content(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"recap")
    assert hash_file(path) == (
        "a3a6e0df57e9e3aef68b6b8f298d34c6bc10a38940f0f7a82f99b29d0dec8f38"
    )


def test_episode_requires_valid_identity():
    with pytest.raises(ValidationError):
        Episode(
            id=new_id(),
            project_id=new_id(),
            ordinal=0,
            source_path="x.mp4",
            source_sha256="a" * 64,
            media=media(),
        )
    with pytest.raises(ValidationError):
        Episode(
            id=new_id(),
            project_id=new_id(),
            ordinal=1,
            source_path="x.mp4",
            source_sha256="nope",
            media=media(),
        )


def test_project_requires_name_and_root():
    with pytest.raises(ValidationError):
        Project(id=new_id(), name="  ", root_path="/tmp/p")
    with pytest.raises(ValidationError):
        Project(id=new_id(), name="p", root_path="")


def test_job_input_hash_is_stable_and_input_sensitive():
    a = job_input_hash("IMPORT", "EPISODE_SOURCE", "a" * 64, "ffprobe-1")
    b = job_input_hash("IMPORT", "EPISODE_SOURCE", "a" * 64, "ffprobe-1")
    c = job_input_hash("IMPORT", "EPISODE_SOURCE", "a" * 64, "ffprobe-2")
    assert a == b != c


def test_terminal_states_cannot_transition():
    for state in (JobState.SUCCEEDED, JobState.CANCELLED):
        for target in JobState:
            assert not can_transition(state, target)
            with pytest.raises(ValidationError):
                assert_transition(state, target)


def test_running_attempt_counter_increases():
    job = Job(
        id=new_id(),
        project_id=new_id(),
        stage="IMPORT",
        scope_type="EPISODE_SOURCE",
        scope_id="a" * 64,
        input_hash="h",
    )
    running = job.with_state(JobState.READY).with_state(JobState.RUNNING)
    assert running.attempts == 1
    resumed = running.with_state(JobState.INTERRUPTED).with_state(JobState.READY)
    assert resumed.with_state(JobState.RUNNING).attempts == 2


def test_job_rejects_invalid_construction():
    def build(**overrides):
        base = dict(
            id=new_id(),
            project_id=new_id(),
            stage="IMPORT",
            scope_type="EPISODE_SOURCE",
            scope_id="a" * 64,
            input_hash="h",
        )
        base.update(overrides)
        return Job(**base)

    with pytest.raises(ValidationError):
        build(stage=" ")
    with pytest.raises(ValidationError):
        build(attempts=-1)
    with pytest.raises(ValidationError):
        build(max_attempts=0)
