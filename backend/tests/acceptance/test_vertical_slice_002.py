"""End-to-end acceptance for Contract 002 using real local FFmpeg/FFprobe.

The AI/STT boundaries use deterministic test adapters; every media artifact is
produced by the real local toolchain and re-probed here, so proxy/audio/keyframe
claims are backed by raw ffprobe output rather than by the adapter's own report.

There is no skip path: an environment without a working FFmpeg fails this gate.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from recap_core.application.import_episode import ImportEpisodeUseCase
from recap_core.application.understand_episode import STAGE_ORDER, UnderstandEpisodeUseCase
from recap_core.domain.identity import hash_file
from recap_core.infrastructure.ffmpeg.ffprobe import FFprobeMediaProber
from recap_core.infrastructure.ffmpeg.media_processor import (
    MAX_PROXY_HEIGHT,
    STT_CHANNELS,
    STT_SAMPLE_RATE,
    FFmpegMediaProcessor,
)
from recap_core.infrastructure.ffmpeg.subprocess_runner import SubprocessCommandRunner
from recap_core.schemas.validator import validate_document
from tests.providers import (
    DeterministicSceneAnalysisProvider,
    DeterministicSTTProvider,
    DeterministicStoryReasoningProvider,
)

FFPROBE_TIMEOUT_S = 60.0


def ffprobe_json(path: Path) -> dict:
    """Raw ffprobe evidence for one generated artifact."""
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=FFPROBE_TIMEOUT_S,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.fixture
def local_media():
    runner = SubprocessCommandRunner()
    prober = FFprobeMediaProber(runner)
    processor = FFmpegMediaProcessor(runner, prober)
    processor.assert_available()
    return prober, processor


@pytest.fixture
def slice_run(connection, project, source_file, local_media):
    """One full pass over the committed fixture with real local media adapters."""
    prober, processor = local_media
    imported = ImportEpisodeUseCase(connection, prober).execute(
        project_id=project.id,
        project_root=Path(project.root_path),
        source_path=source_file,
    )
    use_case = UnderstandEpisodeUseCase(
        connection,
        media_processor=processor,
        stt_provider=DeterministicSTTProvider(),
        scene_provider=DeterministicSceneAnalysisProvider(),
        story_provider=DeterministicStoryReasoningProvider(),
    )
    result = use_case.execute(episode_id=imported.episode.id)
    return imported.episode, result


def artifacts_by_kind(connection, kind: str) -> list:
    return list(
        connection.execute("SELECT * FROM artifacts WHERE kind = ? ORDER BY path", (kind,))
    )


def test_committed_fixture_reaches_a_schema_valid_recap_plan(connection, slice_run):
    episode, result = slice_run

    assert result.executed == tuple(stage.value for stage in STAGE_ORDER)
    plan_document = json.loads(result.plan_path.read_bytes())
    validate_document("recap-plan", plan_document)
    assert plan_document["ordered_events"]
    assert plan_document["estimated_duration_ms"] > 0
    assert plan_document["provenance"]["source_sha256"] == episode.source_sha256

    persisted = connection.execute(
        "SELECT * FROM recap_plans WHERE recap_revision_id = ?", (result.recap_revision_id,)
    ).fetchone()
    assert persisted["id"] == result.plan_id
    assert persisted["estimated_ms"] == plan_document["estimated_duration_ms"]
    planned_rows = connection.execute(
        "SELECT COUNT(*) AS c FROM recap_plan_events WHERE plan_id = ?", (result.plan_id,)
    ).fetchone()["c"]
    assert planned_rows == len(plan_document["ordered_events"])


def test_every_stage_document_validates_against_its_published_schema(connection, slice_run):
    _, result = slice_run
    for kind, schema in (
        ("transcript", "transcript"),
        ("shots", "shots"),
        ("scenes", "scenes"),
        ("events", "events"),
        ("story_graph", "story-graph"),
        ("recap_plan", "recap-plan"),
    ):
        row = artifacts_by_kind(connection, kind)[0]
        validate_document(schema, json.loads(Path(row["path"]).read_bytes()))
    assert result.plan_id


def test_generated_proxy_is_decodable_720p_or_lower_with_source_aspect_ratio(
    connection, slice_run
):
    episode, _ = slice_run
    row = artifacts_by_kind(connection, "media_proxy")[0]
    probe = ffprobe_json(Path(row["path"]))
    video = [s for s in probe["streams"] if s["codec_type"] == "video"]
    assert len(video) == 1
    stream = video[0]
    assert stream["height"] <= MAX_PROXY_HEIGHT
    assert float(probe["format"]["duration"]) > 0

    source = episode.media.video_streams[0]
    source_ratio = source.width / source.height
    proxy_ratio = stream["width"] / stream["height"]
    assert abs(source_ratio - proxy_ratio) < 0.02
    assert stream["height"] <= source.height


def test_generated_stt_audio_is_decodable_mono_16k(connection, slice_run):
    _, _ = slice_run
    row = artifacts_by_kind(connection, "stt_audio")[0]
    probe = ffprobe_json(Path(row["path"]))
    audio = [s for s in probe["streams"] if s["codec_type"] == "audio"]
    assert len(audio) == 1
    assert int(audio[0]["channels"]) == STT_CHANNELS
    assert int(audio[0]["sample_rate"]) == STT_SAMPLE_RATE
    assert float(probe["format"]["duration"]) > 0


def test_at_least_one_keyframe_is_decodable_and_source_timestamped(connection, slice_run):
    episode, result = slice_run
    keyframes = artifacts_by_kind(connection, "keyframe")
    assert keyframes

    shots_document = json.loads(
        Path(artifacts_by_kind(connection, "shots")[0]["path"]).read_bytes()
    )
    timestamps = {
        frame["timestamp_ms"] for shot in shots_document["shots"] for frame in shot["keyframes"]
    }
    assert timestamps
    for timestamp in timestamps:
        assert 0 <= timestamp < episode.duration_ms

    for row in keyframes:
        probe = ffprobe_json(Path(row["path"]))
        video = [s for s in probe["streams"] if s["codec_type"] == "video"]
        assert video and int(video[0]["width"]) > 0
    assert result.analysis_revision_id


def test_every_artifact_carries_a_verified_hash_size_and_cache_identity(connection, slice_run):
    episode, result = slice_run
    rows = list(connection.execute("SELECT * FROM artifacts"))
    kinds = {row["kind"] for row in rows}
    assert {
        "media_metadata",
        "media_proxy",
        "stt_audio",
        "transcript",
        "shots",
        "keyframe",
        "scenes",
        "events",
        "story_graph",
        "recap_plan",
    } <= kinds

    for row in rows:
        path = Path(row["path"])
        assert path.is_file()
        assert path.stat().st_size == row["size"]
        assert hash_file(path) == row["sha256"]
        assert len(row["cache_key"]) == 64
        assert row["producer_version"]
        assert row["episode_id"] == episode.id
        assert row["state"] == "PUBLISHED"

    linked = connection.execute(
        "SELECT COUNT(*) AS c FROM stage_checkpoint_artifacts"
    ).fetchone()["c"]
    assert linked >= len(STAGE_ORDER)
    assert result.analysis_revision_id


def test_analysis_output_lives_under_one_immutable_revision(connection, slice_run):
    _, result = slice_run
    for table in (
        "transcript_segments",
        "shots",
        "scenes",
        "characters",
        "events",
        "evidence_items",
        "plots",
        "story_edges",
    ):
        rows = list(
            connection.execute(
                f"SELECT DISTINCT analysis_revision_id AS r FROM {table}"  # noqa: S608
            )
        )
        assert [row["r"] for row in rows] == [result.analysis_revision_id], table

    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_source_bytes_are_never_modified_by_the_pipeline(connection, slice_run, source_file):
    episode, _ = slice_run
    assert hash_file(source_file) == episode.source_sha256
    assert (
        connection.execute(
            "SELECT source_sha256 FROM episodes WHERE id = ?", (episode.id,)
        ).fetchone()["source_sha256"]
        == episode.source_sha256
    )
