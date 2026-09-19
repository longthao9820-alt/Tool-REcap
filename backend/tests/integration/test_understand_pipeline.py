"""Dependency-aware orchestration: resume, idempotency, invalidation, revisions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from recap_core.application.understand_episode import (
    STAGE_ORDER,
    Stage,
    UnderstandEpisodeUseCase,
    descendants,
)
from recap_core.domain.errors import (
    CheckpointIntegrityError,
    SourceIdentityMismatchError,
    SourceMissingError,
    StageDependencyError,
    ValidationError,
)
from recap_core.domain.recap.profile import RecapProfile, RecapStyle
from recap_core.infrastructure.database.repositories import SqliteArtifactRepository
from recap_core.schemas.validator import validate_document
from tests.providers import DeterministicSTTProvider


def call_counts(providers) -> dict[str, int]:
    processor = providers["media_processor"]
    return {
        "proxy": processor.proxy_calls,
        "audio": processor.audio_calls,
        "shots": processor.shot_calls,
        "stt": providers["stt_provider"].calls,
        "scene": providers["scene_provider"].calls,
        "extract": providers["story_provider"].extract_calls,
        "reason": providers["story_provider"].reason_calls,
    }


STAGE_TO_COUNTERS = {
    Stage.PREPROCESS: ("proxy", "audio"),
    Stage.TRANSCRIBE: ("stt",),
    Stage.DETECT_SHOTS: ("shots",),
    Stage.BUILD_SCENES: ("scene",),
    Stage.EXTRACT_EVENTS: ("extract",),
    Stage.BUILD_STORY_GRAPH: ("reason",),
    Stage.PLAN_RECAP: (),
}


def artifact_identities(connection) -> dict[str, tuple[str, str, int]]:
    return {
        row["id"]: (row["kind"], row["sha256"], row["size"])
        for row in connection.execute("SELECT * FROM artifacts")
    }


# -- happy path --------------------------------------------------------------


def test_full_run_persists_one_analysis_and_one_recap_revision(
    connection, episode, understand, providers
):
    result = understand().execute(episode_id=episode.id)

    assert result.executed == tuple(stage.value for stage in STAGE_ORDER)
    assert result.reused == ()
    assert connection.execute(
        "SELECT COUNT(*) AS c FROM analysis_revisions"
    ).fetchone()["c"] == 1
    assert connection.execute("SELECT COUNT(*) AS c FROM recap_revisions").fetchone()["c"] == 1
    assert connection.execute(
        "SELECT status FROM analysis_revisions"
    ).fetchone()["status"] == "COMPLETE"

    for table in ("transcript_segments", "shots", "scenes", "events", "plots", "recap_plans"):
        rows = connection.execute(
            f"SELECT COUNT(*) AS c FROM {table}"  # noqa: S608 - fixed identifier list
        ).fetchone()["c"]
        assert rows > 0, table

    revisions = {
        row["analysis_revision_id"]
        for row in connection.execute(
            "SELECT analysis_revision_id FROM events"
            " UNION SELECT analysis_revision_id FROM scenes"
            " UNION SELECT analysis_revision_id FROM shots"
            " UNION SELECT analysis_revision_id FROM transcript_segments"
            " UNION SELECT analysis_revision_id FROM plots"
            " UNION SELECT analysis_revision_id FROM story_edges"
        )
    }
    assert revisions == {result.analysis_revision_id}

    plan = json.loads(result.plan_path.read_bytes())
    validate_document("recap-plan", plan)
    assert plan["analysis_revision_id"] == result.analysis_revision_id
    assert plan["recap_revision_id"] == result.recap_revision_id
    assert plan["ordered_events"]


def test_every_artifact_is_bound_to_source_identity_and_provenance(
    connection, episode, understand
):
    result = understand().execute(episode_id=episode.id)
    rows = list(connection.execute("SELECT * FROM artifacts"))
    assert len(rows) >= 7
    for row in rows:
        path = Path(row["path"])
        assert path.is_file()
        assert path.stat().st_size == row["size"]
        assert row["cache_key"] and row["producer_version"]
        assert row["episode_id"] == episode.id

    checkpoints = list(connection.execute("SELECT * FROM stage_checkpoints"))
    assert {row["stage"] for row in checkpoints} == {stage.value for stage in STAGE_ORDER}
    for row in checkpoints:
        assert row["state"] == "SUCCEEDED"
        assert len(row["input_hash"]) == 64

    for name in ("transcript", "shots", "scenes", "events", "story_graph", "recap_plan"):
        artifact = connection.execute(
            "SELECT * FROM artifacts WHERE kind = ? LIMIT 1", (name,)
        ).fetchone()
        document = json.loads(Path(artifact["path"]).read_bytes())
        provenance = document["provenance"]
        assert provenance["source_sha256"] == episode.source_sha256
        assert provenance["producer"] and provenance["producer_version"]

    artifacts = SqliteArtifactRepository(connection)
    story_graph = connection.execute(
        "SELECT * FROM artifacts WHERE kind = 'story_graph'"
    ).fetchone()
    upstream = artifacts.dependency_ids(story_graph["id"])
    assert upstream
    assert all(artifacts.get(artifact_id) is not None for artifact_id in upstream)
    events_artifact = connection.execute(
        "SELECT id FROM artifacts WHERE kind = 'events'"
    ).fetchone()["id"]
    assert events_artifact in upstream
    assert result.executed


def test_rerunning_identical_inputs_is_idempotent(connection, episode, understand, providers):
    first = understand().execute(episode_id=episode.id)
    before_counts = call_counts(providers)
    before_artifacts = artifact_identities(connection)

    second = understand().execute(episode_id=episode.id)

    assert second.executed == ()
    assert second.reused == tuple(stage.value for stage in STAGE_ORDER)
    assert second.analysis_revision_id == first.analysis_revision_id
    assert second.recap_revision_id == first.recap_revision_id
    assert second.plan_id == first.plan_id
    assert call_counts(providers) == before_counts
    assert artifact_identities(connection) == before_artifacts


# -- resume ------------------------------------------------------------------


@pytest.mark.parametrize("interrupted", STAGE_ORDER, ids=[s.value for s in STAGE_ORDER])
def test_interruption_resumes_from_the_first_incomplete_stage(
    connection, episode, understand, providers, interrupted
):
    def stop_at(stage: str) -> None:
        if stage == interrupted.value:
            raise RuntimeError(f"simulated interruption at {stage}")

    with pytest.raises(RuntimeError):
        understand(before_publish=stop_at).execute(episode_id=episode.id)

    predecessors = STAGE_ORDER[: STAGE_ORDER.index(interrupted)]
    counts_after_crash = call_counts(providers)
    artifacts_after_crash = artifact_identities(connection)
    assert set(
        row["stage"] for row in connection.execute("SELECT stage FROM stage_checkpoints")
    ) == {stage.value for stage in predecessors}

    resumed = understand().execute(episode_id=episode.id)

    assert resumed.reused == tuple(stage.value for stage in predecessors)
    assert resumed.executed == tuple(
        stage.value for stage in STAGE_ORDER[STAGE_ORDER.index(interrupted) :]
    )
    after = call_counts(providers)
    for stage in predecessors:
        for counter in STAGE_TO_COUNTERS[stage]:
            assert after[counter] == counts_after_crash[counter], counter
    for artifact_id, identity in artifacts_after_crash.items():
        assert artifact_identities(connection)[artifact_id] == identity


def test_resume_publishes_no_duplicate_rows(connection, episode, understand):
    def stop_at(stage: str) -> None:
        if stage == Stage.EXTRACT_EVENTS.value:
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError):
        understand(before_publish=stop_at).execute(episode_id=episode.id)
    assert connection.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"] == 0

    understand().execute(episode_id=episode.id)
    events = connection.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
    distinct = connection.execute("SELECT COUNT(DISTINCT id) AS c FROM events").fetchone()["c"]
    assert events == distinct > 0
    assert (
        connection.execute("SELECT COUNT(*) AS c FROM stage_checkpoints").fetchone()["c"]
        == len(STAGE_ORDER)
    )


# -- invalidation ------------------------------------------------------------


def test_corrupt_stage_artifact_forces_that_stage_and_its_descendants_to_rebuild(
    connection, episode, understand, providers
):
    first = understand().execute(episode_id=episode.id)
    transcript = connection.execute(
        "SELECT * FROM artifacts WHERE kind = 'transcript'"
    ).fetchone()
    Path(transcript["path"]).write_bytes(b"{}")
    before = call_counts(providers)

    second = understand().execute(episode_id=episode.id)

    assert second.executed == (
        Stage.TRANSCRIBE.value,
        Stage.BUILD_SCENES.value,
        Stage.EXTRACT_EVENTS.value,
        Stage.BUILD_STORY_GRAPH.value,
        Stage.PLAN_RECAP.value,
    )
    assert second.reused == (Stage.PREPROCESS.value, Stage.DETECT_SHOTS.value)
    after = call_counts(providers)
    assert (after["proxy"], after["audio"], after["shots"]) == (
        before["proxy"],
        before["audio"],
        before["shots"],
    )
    assert after["stt"] == before["stt"] + 1
    assert second.analysis_revision_id == first.analysis_revision_id
    assert Path(transcript["path"]).read_bytes() != b"{}"


def test_deleted_keyframe_invalidates_shot_detection_without_orphaning_evidence(
    connection, episode, understand
):
    understand().execute(episode_id=episode.id)
    keyframe = connection.execute("SELECT * FROM artifacts WHERE kind = 'keyframe'").fetchone()
    Path(keyframe["path"]).unlink()

    result = understand().execute(episode_id=episode.id)

    assert Stage.DETECT_SHOTS.value in result.executed
    assert Stage.PREPROCESS.value in result.reused
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    remaining = connection.execute(
        "SELECT COUNT(*) AS c FROM evidence_items WHERE artifact_id IS NOT NULL"
        " AND artifact_id NOT IN (SELECT id FROM artifacts)"
    ).fetchone()["c"]
    assert remaining == 0


def test_changing_the_stt_version_creates_a_new_analysis_revision(
    connection, episode, understand, providers
):
    class NewerSTT(DeterministicSTTProvider):
        @property
        def version(self) -> str:
            return "deterministic-stt-2"

    first = understand().execute(episode_id=episode.id)
    second = understand(stt_provider=NewerSTT()).execute(episode_id=episode.id)

    assert second.analysis_revision_id != first.analysis_revision_id
    assert connection.execute(
        "SELECT COUNT(*) AS c FROM analysis_revisions"
    ).fetchone()["c"] == 2
    assert second.executed == tuple(stage.value for stage in STAGE_ORDER)
    first_rows = connection.execute(
        "SELECT COUNT(*) AS c FROM transcript_segments WHERE analysis_revision_id = ?",
        (first.analysis_revision_id,),
    ).fetchone()["c"]
    assert first_rows > 0


# -- recap revisions ---------------------------------------------------------


def test_profile_change_reuses_the_exact_analysis_revision(
    connection, episode, understand, providers
):
    first = understand().execute(episode_id=episode.id)
    analysis_artifacts = {
        row["id"]: (row["sha256"], row["size"])
        for row in connection.execute("SELECT * FROM artifacts WHERE kind <> 'recap_plan'")
    }
    before = call_counts(providers)

    second = understand().execute(
        episode_id=episode.id,
        profile=RecapProfile(style=RecapStyle.FAST_FOCUSED, compression=0.4),
    )

    assert second.analysis_revision_id == first.analysis_revision_id
    assert second.recap_revision_id != first.recap_revision_id
    assert second.plan_id != first.plan_id
    assert second.executed == (Stage.PLAN_RECAP.value,)
    assert call_counts(providers) == before
    assert {
        row["id"]: (row["sha256"], row["size"])
        for row in connection.execute("SELECT * FROM artifacts WHERE kind <> 'recap_plan'")
    } == analysis_artifacts
    assert connection.execute("SELECT COUNT(*) AS c FROM recap_plans").fetchone()["c"] == 2
    assert Path(first.plan_path).is_file()


def test_same_profile_reuses_the_same_recap_revision(connection, episode, understand):
    profile = RecapProfile(style=RecapStyle.CINEMATIC)
    first = understand().execute(episode_id=episode.id, profile=profile)
    second = understand().execute(episode_id=episode.id, profile=profile)
    assert second.recap_revision_id == first.recap_revision_id
    assert second.executed == ()
    assert connection.execute("SELECT COUNT(*) AS c FROM recap_plans").fetchone()["c"] == 1


# -- fail-closed inputs ------------------------------------------------------


def test_unknown_episode_fails_typed(understand):
    with pytest.raises(ValidationError):
        understand().execute(episode_id="ghost")


def test_missing_source_fails_before_any_stage_runs(connection, episode, understand, providers):
    Path(episode.source_path).unlink()
    with pytest.raises(SourceMissingError):
        understand().execute(episode_id=episode.id)
    assert call_counts(providers)["proxy"] == 0
    assert connection.execute("SELECT COUNT(*) AS c FROM stage_checkpoints").fetchone()["c"] == 0


def test_source_drift_fails_closed_before_any_stage_runs(connection, episode, understand):
    source = Path(episode.source_path)
    source.write_bytes(source.read_bytes() + b"drifted")
    with pytest.raises(SourceIdentityMismatchError):
        understand().execute(episode_id=episode.id)
    assert connection.execute("SELECT COUNT(*) AS c FROM stage_checkpoints").fetchone()["c"] == 0


def test_source_drift_during_the_pipeline_stops_publication(connection, episode, understand):
    source = Path(episode.source_path)

    def drift(stage: str) -> None:
        if stage == Stage.DETECT_SHOTS.value:
            source.write_bytes(source.read_bytes() + b"drifted")

    with pytest.raises(SourceIdentityMismatchError):
        understand(before_publish=drift).execute(episode_id=episode.id)
    stages = {
        row["stage"] for row in connection.execute("SELECT stage FROM stage_checkpoints")
    }
    assert Stage.DETECT_SHOTS.value not in stages
    assert connection.execute("SELECT COUNT(*) AS c FROM shots").fetchone()["c"] == 0


def test_stage_dag_descendants_are_exact():
    assert descendants(Stage.DETECT_SHOTS) == (
        Stage.PLAN_RECAP,
        Stage.BUILD_STORY_GRAPH,
        Stage.EXTRACT_EVENTS,
        Stage.BUILD_SCENES,
    )
    assert descendants(Stage.TRANSCRIBE) == (
        Stage.PLAN_RECAP,
        Stage.BUILD_STORY_GRAPH,
        Stage.EXTRACT_EVENTS,
        Stage.BUILD_SCENES,
    )
    assert Stage.DETECT_SHOTS not in descendants(Stage.TRANSCRIBE)
    assert descendants(Stage.PLAN_RECAP) == ()


def test_empty_transcript_output_fails_closed(connection, episode, understand):
    class SilentSTT(DeterministicSTTProvider):
        def transcribe(self, audio_path, language, options=None):
            self.calls += 1
            from recap_core.ports.stt import TranscriptDTO

            return TranscriptDTO(
                language="vi", segments=(), model=self.model, provider_version=self.version
            )

    from recap_core.domain.errors import ProviderOutputInvalidError

    with pytest.raises(ProviderOutputInvalidError):
        understand(stt_provider=SilentSTT()).execute(episode_id=episode.id)
    assert connection.execute(
        "SELECT COUNT(*) AS c FROM transcript_segments"
    ).fetchone()["c"] == 0


def test_missing_proxy_file_rebuilds_preprocess_only(connection, episode, understand):
    first = understand().execute(episode_id=episode.id)
    proxy = connection.execute("SELECT * FROM artifacts WHERE kind = 'media_proxy'").fetchone()
    Path(proxy["path"]).unlink()

    rerun = understand().execute(episode_id=episode.id)

    assert rerun.executed[0] == Stage.PREPROCESS.value
    assert rerun.reused == ()
    assert rerun.analysis_revision_id == first.analysis_revision_id
    assert Path(
        connection.execute(
            "SELECT path FROM artifacts WHERE kind = 'media_proxy'"
        ).fetchone()["path"]
    ).is_file()


def test_row_counts_disagreeing_with_a_checkpoint_force_a_rebuild(
    connection, episode, understand, providers
):
    """A checkpoint is only evidence while the rows it claims still exist."""
    understand().execute(episode_id=episode.id)
    before = call_counts(providers)
    connection.execute("DELETE FROM plots")

    rerun = understand().execute(episode_id=episode.id)

    assert rerun.executed == (Stage.BUILD_STORY_GRAPH.value, Stage.PLAN_RECAP.value)
    assert Stage.EXTRACT_EVENTS.value in rerun.reused
    assert call_counts(providers)["reason"] == before["reason"] + 1
    assert call_counts(providers)["extract"] == before["extract"]
    assert connection.execute("SELECT COUNT(*) AS c FROM plots").fetchone()["c"] > 0


def test_transcript_rows_cannot_be_deleted_while_evidence_references_them(
    connection, episode, understand
):
    """Referential integrity, not application code, protects published evidence."""
    import sqlite3

    understand().execute(episode_id=episode.id)
    referenced = connection.execute(
        "SELECT COUNT(*) AS c FROM evidence_items WHERE transcript_id IS NOT NULL"
    ).fetchone()["c"]
    assert referenced > 0
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM transcript_segments")


def test_stage_dependency_error_is_raised_without_predecessor_output(
    connection, episode, understand
):
    use_case = understand()
    result = use_case.execute(episode_id=episode.id)
    with pytest.raises(StageDependencyError):
        use_case.load_story_graph("unknown-revision", _plan_provenance(episode))
    assert result.plan_id


def _plan_provenance(episode):
    from recap_core.domain.provenance import Provenance

    return Provenance(
        source_sha256=episode.source_sha256, producer="test", producer_version="1"
    )


def test_plot_count_follows_the_discovered_event_set(connection, episode, understand):
    """Plot discovery is variable: more events yield more plots, never a fixed count."""
    from tests.providers import DeterministicStoryReasoningProvider

    class RicherStory(DeterministicStoryReasoningProvider):
        @property
        def version(self) -> str:
            return "deterministic-story-2"

    small = understand().execute(episode_id=episode.id)
    small_plots, small_edges = _story_counts(connection, small.analysis_revision_id)

    rich = understand(
        story_provider=RicherStory(events_per_scene=6, plot_size=2)
    ).execute(episode_id=episode.id)
    rich_plots, rich_edges = _story_counts(connection, rich.analysis_revision_id)

    assert rich.analysis_revision_id != small.analysis_revision_id
    assert rich_plots > small_plots >= 1
    assert rich_edges > small_edges

    graph = json.loads(
        Path(
            connection.execute(
                "SELECT a.path AS path FROM artifacts a"
                " JOIN stage_checkpoint_artifacts l ON l.artifact_id = a.id"
                " JOIN stage_checkpoints c ON c.id = l.checkpoint_id"
                " WHERE a.kind = 'story_graph' AND c.scope_id = ?",
                (rich.analysis_revision_id,),
            ).fetchone()["path"]
        ).read_bytes()
    )
    validate_document("story-graph", graph)
    assert len(graph["plots"]) == rich_plots
    assert {edge["relation"] for edge in graph["edges"]} >= {"CAUSE", "PAYOFF"}


def _story_counts(connection, revision_id: str) -> tuple[int, int]:
    plots = connection.execute(
        "SELECT COUNT(*) AS c FROM plots WHERE analysis_revision_id = ?", (revision_id,)
    ).fetchone()["c"]
    edges = connection.execute(
        "SELECT COUNT(*) AS c FROM story_edges WHERE analysis_revision_id = ?", (revision_id,)
    ).fetchone()["c"]
    return plots, edges


def test_unavailable_media_capability_fails_typed_without_any_checkpoint(
    connection, episode, understand
):
    from recap_core.domain.errors import ProbeUnavailableError
    from recap_core.infrastructure.ffmpeg.ffprobe import FFprobeMediaProber
    from recap_core.infrastructure.ffmpeg.media_processor import FFmpegMediaProcessor
    from tests.support import StubCommandRunner

    runner = StubCommandRunner(raises=FileNotFoundError("ffmpeg"))
    unavailable = FFmpegMediaProcessor(runner, FFprobeMediaProber(runner))
    with pytest.raises(ProbeUnavailableError):
        understand(media_processor=unavailable).execute(episode_id=episode.id)
    assert connection.execute("SELECT COUNT(*) AS c FROM stage_checkpoints").fetchone()["c"] == 0
    assert connection.execute("SELECT COUNT(*) AS c FROM artifacts WHERE kind <> "
                              "'media_metadata'").fetchone()["c"] == 0


def test_unavailable_stt_capability_fails_typed(connection, episode, understand):
    from recap_core.domain.errors import ProviderUnavailableError
    from recap_core.infrastructure.providers.faster_whisper_stt import (
        LocalFasterWhisperProvider,
    )

    offline = LocalFasterWhisperProvider()
    assert offline.capability().available is False
    with pytest.raises(ProviderUnavailableError):
        understand(stt_provider=offline).execute(episode_id=episode.id)
    stages = {row["stage"] for row in connection.execute("SELECT stage FROM stage_checkpoints")}
    assert stages == {Stage.PREPROCESS.value}
    assert connection.execute(
        "SELECT COUNT(*) AS c FROM transcript_segments"
    ).fetchone()["c"] == 0


def test_scene_packages_are_reconstructible_from_persisted_rows(
    connection, episode, understand, providers
):
    """The bounded context a provider saw is fully recoverable from the revision."""
    from recap_core.application.understand_episode import Stage as _Stage
    from recap_core.infrastructure.database.analysis_repositories import (
        SqliteSceneRepository,
        SqliteShotRepository,
        SqliteTranscriptRepository,
    )

    use_case = understand()
    result = use_case.execute(episode_id=episode.id)
    seen = providers["scene_provider"].packages
    assert seen

    transcript_document = json.loads(
        Path(
            connection.execute(
                "SELECT path FROM artifacts WHERE kind = 'transcript'"
            ).fetchone()["path"]
        ).read_bytes()
    )
    transcript = SqliteTranscriptRepository(connection).load(
        result.analysis_revision_id,
        language=transcript_document["language"],
        duration_ms=transcript_document["duration_ms"],
        audio_sha256=transcript_document["audio_sha256"],
        provenance=_plan_provenance(episode),
    )
    shots = {shot.id: shot for shot in SqliteShotRepository(connection).load(
        result.analysis_revision_id
    )}
    scenes = SqliteSceneRepository(connection).load(result.analysis_revision_id)
    assert len(scenes) == len(seen)

    for scene, package in zip(scenes, seen):
        assert scene.range == package.range
        assert scene.shot_ids == package.shot_ids
        assert tuple(
            frame for shot_id in scene.shot_ids for frame in shots[shot_id].keyframes
        ) == package.keyframes
        assert tuple(s.id for s in transcript.slice(scene.range)) == tuple(
            s.id for s in package.transcript_slice
        )
    assert _Stage.BUILD_SCENES.value in result.executed
