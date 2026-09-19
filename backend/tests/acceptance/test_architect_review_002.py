"""Architect adversarial pack for the episode-understanding vertical slice (V1-V6)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from recap_core.application.composition import (
    assert_production_provider,
    build_scene_analysis_provider,
    build_story_reasoning_provider,
    build_stt_provider,
)
from recap_core.application.understand_episode import STAGE_ORDER, Stage
from recap_core.domain.errors import (
    ProviderNotAuthorizedError,
    RevisionMismatchError,
    StageDependencyError,
)
from recap_core.domain.identity import hash_file, new_id
from recap_core.domain.provenance import Provenance
from recap_core.domain.recap.profile import RecapProfile, RecapStyle
from recap_core.domain.story.graph import EdgeRelation, StoryEdge, StoryGraph
from recap_core.infrastructure.database.analysis_repositories import (
    SqliteEventRepository,
    SqliteStoryRepository,
)
from tests.providers import (
    DeterministicSceneAnalysisProvider,
    DeterministicSTTProvider,
    DeterministicStoryReasoningProvider,
)


def checkpoint(connection, stage: Stage) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM stage_checkpoints WHERE stage = ?", (stage.value,)
    ).fetchone()
    assert row is not None, stage.value
    return row


def event_ids(connection) -> set[str]:
    return {row["id"] for row in connection.execute("SELECT id FROM events")}


# -- V1: forged completion cannot manufacture downstream PASS ----------------


def test_v1_inflated_checkpoint_counts_cannot_manufacture_a_plan(
    connection, episode, understand, providers
):
    understand().execute(episode_id=episode.id)
    real_events = event_ids(connection)
    before_extract = providers["story_provider"].extract_calls

    row = checkpoint(connection, Stage.EXTRACT_EVENTS)
    forged = json.loads(row["output_json"])
    forged["counts"] = {"events": 999, "evidence_items": 999}
    connection.execute(
        "UPDATE stage_checkpoints SET output_json = ? WHERE id = ?", (json.dumps(forged), row["id"])
    )

    result = understand().execute(episode_id=episode.id)

    assert Stage.EXTRACT_EVENTS.value in result.executed
    assert providers["story_provider"].extract_calls == before_extract + 1
    plan = json.loads(result.plan_path.read_bytes())
    planned = {entry["event_id"] for entry in plan["ordered_events"]}
    assert planned <= event_ids(connection)
    assert len(event_ids(connection)) == len(real_events)
    assert connection.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"] < 999


def test_v1_fabricated_checkpoint_without_artifacts_is_not_evidence(
    connection, episode, understand, providers
):
    understand().execute(episode_id=episode.id)
    real = checkpoint(connection, Stage.BUILD_STORY_GRAPH)
    before_reason = providers["story_provider"].reason_calls

    connection.execute("DELETE FROM story_edges")
    connection.execute("DELETE FROM plots")
    connection.execute("DELETE FROM stage_checkpoints WHERE id = ?", (real["id"],))
    connection.execute(
        "INSERT INTO stage_checkpoints(id, stage, scope_type, scope_id, input_hash, state,"
        " output_json, created_at) VALUES (?, ?, ?, ?, ?, 'SUCCEEDED', ?, datetime('now'))",
        (
            new_id(),
            real["stage"],
            real["scope_type"],
            real["scope_id"],
            real["input_hash"],
            json.dumps(
                {
                    "artifacts": {},
                    "artifact_count": 0,
                    "counts": {"plots": 42, "story_edges": 42},
                    "analysis_complete": True,
                }
            ),
        ),
    )

    result = understand().execute(episode_id=episode.id)

    assert Stage.BUILD_STORY_GRAPH.value in result.executed
    assert providers["story_provider"].reason_calls == before_reason + 1
    plots, edges = SqliteStoryRepository(connection).counts(result.analysis_revision_id)
    assert 0 < plots < 42
    assert edges < 42
    plan = json.loads(result.plan_path.read_bytes())
    assert {entry["event_id"] for entry in plan["ordered_events"]} <= event_ids(connection)


# -- V2: no cross-revision StoryGraph references -----------------------------


def test_v2_story_graph_rejects_events_from_another_analysis_revision(
    connection, episode, understand
):
    class NewerSTT(DeterministicSTTProvider):
        @property
        def version(self) -> str:
            return "deterministic-stt-2"

    first = understand().execute(episode_id=episode.id)
    second = understand(stt_provider=NewerSTT()).execute(episode_id=episode.id)
    assert first.analysis_revision_id != second.analysis_revision_id

    events = SqliteEventRepository(connection)
    first_events = events.load(first.analysis_revision_id)
    second_events = events.load(second.analysis_revision_id)
    assert first_events and second_events

    provenance = Provenance(
        source_sha256=episode.source_sha256, producer="test", producer_version="1"
    )
    with pytest.raises(RevisionMismatchError):
        StoryGraph(
            analysis_revision_id=first.analysis_revision_id,
            events=first_events + second_events[:1],
            plots=(),
            edges=(),
            provenance=provenance,
        )
    with pytest.raises(RevisionMismatchError):
        StoryGraph(
            analysis_revision_id=first.analysis_revision_id,
            events=first_events,
            plots=(),
            edges=(
                StoryEdge(
                    id=new_id(),
                    analysis_revision_id=first.analysis_revision_id,
                    from_event_id=first_events[0].id,
                    to_event_id=second_events[0].id,
                    relation=EdgeRelation.CAUSE,
                ),
            ),
            provenance=provenance,
        )


def test_v2_database_refuses_a_cross_revision_edge_or_evidence_row(
    connection, episode, understand
):
    class NewerSTT(DeterministicSTTProvider):
        @property
        def version(self) -> str:
            return "deterministic-stt-2"

    first = understand().execute(episode_id=episode.id)
    second = understand(stt_provider=NewerSTT()).execute(episode_id=episode.id)
    events = SqliteEventRepository(connection)
    from_event = events.load(first.analysis_revision_id)[0]
    foreign_event = events.load(second.analysis_revision_id)[0]

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO story_edges(id, analysis_revision_id, from_event_id, to_event_id,"
            " relation, confidence) VALUES (?, ?, ?, ?, 'CAUSE', 1.0)",
            (new_id(), first.analysis_revision_id, from_event.id, foreign_event.id),
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO evidence_items(id, event_id, analysis_revision_id, type, start_ms,"
            " end_ms, confidence) VALUES (?, ?, ?, 'TIME_RANGE', 0, 10, 1.0)",
            (new_id(), foreign_event.id, first.analysis_revision_id),
        )


# -- V3: test adapters are unreachable from production composition -----------


def test_v3_production_composition_refuses_every_test_adapter():
    for provider in (
        DeterministicSTTProvider(),
        DeterministicSceneAnalysisProvider(),
        DeterministicStoryReasoningProvider(),
    ):
        with pytest.raises(ProviderNotAuthorizedError):
            assert_production_provider(provider)
        for builder in (
            build_stt_provider,
            build_scene_analysis_provider,
            build_story_reasoning_provider,
        ):
            with pytest.raises(ProviderNotAuthorizedError):
                builder(provider.name)


# -- V4: a profile change cannot touch analysis output -----------------------


def test_v4_profile_only_change_leaves_analysis_artifacts_byte_identical(
    connection, episode, understand, providers
):
    first = understand().execute(episode_id=episode.id)

    def analysis_state():
        return {
            row["id"]: (
                row["kind"],
                row["sha256"],
                row["size"],
                Path(row["path"]).stat().st_mtime_ns,
            )
            for row in connection.execute(
                "SELECT * FROM artifacts WHERE kind <> 'recap_plan'"
            )
        }

    before_artifacts = analysis_state()
    before_checkpoints = {
        row["stage"]: (row["id"], row["input_hash"])
        for row in connection.execute(
            "SELECT * FROM stage_checkpoints WHERE stage <> 'PLAN_RECAP'"
        )
    }
    before_rows = {
        table: connection.execute(
            f"SELECT COUNT(*) AS c FROM {table}"  # noqa: S608 - fixed identifier list
        ).fetchone()["c"]
        for table in ("transcript_segments", "shots", "scenes", "events", "plots", "story_edges")
    }

    second = understand().execute(
        episode_id=episode.id, profile=RecapProfile(style=RecapStyle.CINEMATIC, compression=0.6)
    )

    assert second.analysis_revision_id == first.analysis_revision_id
    assert second.executed == (Stage.PLAN_RECAP.value,)
    assert analysis_state() == before_artifacts
    assert {
        row["stage"]: (row["id"], row["input_hash"])
        for row in connection.execute(
            "SELECT * FROM stage_checkpoints WHERE stage <> 'PLAN_RECAP'"
        )
    } == before_checkpoints
    assert {
        table: connection.execute(
            f"SELECT COUNT(*) AS c FROM {table}"  # noqa: S608 - fixed identifier list
        ).fetchone()["c"]
        for table in before_rows
    } == before_rows
    assert connection.execute(
        "SELECT COUNT(*) AS c FROM analysis_revisions"
    ).fetchone()["c"] == 1


# -- V5: a corrupt predecessor cannot be skipped via a stale SUCCEEDED row ----


def test_v5_corrupt_proxy_of_the_same_size_is_not_reused(
    connection, episode, understand, providers
):
    understand().execute(episode_id=episode.id)
    proxy = connection.execute(
        "SELECT * FROM artifacts WHERE kind = 'media_proxy'"
    ).fetchone()
    path = Path(proxy["path"])
    original = path.read_bytes()
    path.write_bytes(b"\x00" * len(original))
    assert path.stat().st_size == proxy["size"]
    assert hash_file(path) != proxy["sha256"]
    assert (
        checkpoint(connection, Stage.PREPROCESS)["state"] == "SUCCEEDED"
    ), "the stale checkpoint still claims success"
    before = providers["media_processor"].proxy_calls

    result = understand().execute(episode_id=episode.id)

    assert Stage.PREPROCESS.value in result.executed
    assert providers["media_processor"].proxy_calls == before + 1
    rebuilt = connection.execute(
        "SELECT * FROM artifacts WHERE kind = 'media_proxy'"
    ).fetchone()
    assert hash_file(Path(rebuilt["path"])) == rebuilt["sha256"]
    assert Path(rebuilt["path"]).read_bytes() == original


def test_v5_a_missing_downstream_artifact_never_yields_a_stale_plan(
    connection, episode, understand
):
    first = understand().execute(episode_id=episode.id)
    story_graph = connection.execute(
        "SELECT * FROM artifacts WHERE kind = 'story_graph'"
    ).fetchone()
    Path(story_graph["path"]).unlink()

    second = understand().execute(episode_id=episode.id)

    assert Stage.BUILD_STORY_GRAPH.value in second.executed
    assert Stage.PLAN_RECAP.value in second.executed
    assert second.plan_id != first.plan_id
    assert Path(
        connection.execute(
            "SELECT path FROM artifacts WHERE kind = 'story_graph'"
        ).fetchone()["path"]
    ).is_file()


# -- V6: retry/resume publishes no duplicates and overwrites no valid revision -


@pytest.mark.parametrize("interrupted", STAGE_ORDER, ids=[s.value for s in STAGE_ORDER])
def test_v6_resume_never_duplicates_rows_or_checkpoints(
    connection, episode, understand, interrupted
):
    def stop_at(stage: str) -> None:
        if stage == interrupted.value:
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError):
        understand(before_publish=stop_at).execute(episode_id=episode.id)
    understand().execute(episode_id=episode.id)

    stages = [row["stage"] for row in connection.execute("SELECT stage FROM stage_checkpoints")]
    assert sorted(stages) == sorted(stage.value for stage in STAGE_ORDER)
    for table in (
        "transcript_segments",
        "shots",
        "scenes",
        "characters",
        "events",
        "evidence_items",
        "plots",
        "story_edges",
        "recap_plans",
    ):
        total = connection.execute(
            f"SELECT COUNT(*) AS c FROM {table}"  # noqa: S608 - fixed identifier list
        ).fetchone()["c"]
        distinct = connection.execute(
            f"SELECT COUNT(DISTINCT id) AS c FROM {table}"  # noqa: S608
        ).fetchone()["c"]
        assert total == distinct, table
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v6_planning_a_second_profile_does_not_overwrite_the_first_plan(
    connection, episode, understand
):
    first = understand().execute(episode_id=episode.id)
    first_bytes = Path(first.plan_path).read_bytes()

    second = understand().execute(
        episode_id=episode.id, profile=RecapProfile(style=RecapStyle.FAST_FOCUSED)
    )

    assert second.plan_path != first.plan_path
    assert Path(first.plan_path).read_bytes() == first_bytes
    assert connection.execute(
        "SELECT COUNT(*) AS c FROM recap_plans"
    ).fetchone()["c"] == 2
    assert connection.execute(
        "SELECT COUNT(*) AS c FROM recap_plans WHERE id = ?", (first.plan_id,)
    ).fetchone()["c"] == 1
    assert connection.execute(
        "SELECT COUNT(*) AS c FROM stage_checkpoints WHERE stage = 'PLAN_RECAP'"
    ).fetchone()["c"] == 2


def test_v6_replanning_the_same_profile_reuses_the_immutable_plan(
    connection, episode, understand
):
    first = understand().execute(episode_id=episode.id)
    stamp = Path(first.plan_path).stat().st_mtime_ns
    second = understand().execute(episode_id=episode.id)
    assert second.plan_id == first.plan_id
    assert second.executed == ()
    assert Path(first.plan_path).stat().st_mtime_ns == stamp


def test_story_graph_cannot_be_rebuilt_from_an_unknown_revision(connection, episode, understand):
    use_case = understand()
    use_case.execute(episode_id=episode.id)
    with pytest.raises(StageDependencyError):
        use_case.load_story_graph(
            "not-a-revision",
            Provenance(
                source_sha256=episode.source_sha256, producer="t", producer_version="1"
            ),
        )


def test_v1_schema_invalid_bytes_with_a_matching_hash_are_not_reusable_evidence(
    connection, episode, understand, providers
):
    """Even a hash that was tampered to agree cannot revive a schema-invalid document."""
    understand().execute(episode_id=episode.id)
    row = connection.execute("SELECT * FROM artifacts WHERE kind = 'events'").fetchone()
    path = Path(row["path"])
    forged = json.dumps({"schema_version": 1, "analysis_revision_id": "x"}).encode("utf-8")
    path.write_bytes(forged)
    connection.execute(
        "UPDATE artifacts SET sha256 = ?, size = ? WHERE id = ?",
        (hash_file(path), len(forged), row["id"]),
    )
    before = providers["story_provider"].extract_calls

    result = understand().execute(episode_id=episode.id)

    assert Stage.EXTRACT_EVENTS.value in result.executed
    assert providers["story_provider"].extract_calls == before + 1
    rebuilt = connection.execute("SELECT * FROM artifacts WHERE kind = 'events'").fetchone()
    document = json.loads(Path(rebuilt["path"]).read_bytes())
    assert document["analysis_revision_id"] == result.analysis_revision_id
    assert document["events"]
