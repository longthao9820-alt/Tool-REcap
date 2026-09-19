"""SQLite repositories for analysis revisions, stage checkpoints and story rows.

Every write here is called from inside the orchestrator's publication transaction.
Cross-revision substitution is rejected by the composite foreign keys declared in
migration 0002, so these repositories never re-implement that check in Python.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable, Sequence

from ...domain.analysis.revision import AnalysisRevision, RecapRevision, RevisionStatus
from ...domain.provenance import Provenance
from ...domain.recap.plan import RecapPlan
from ...domain.scene.scene import Scene
from ...domain.scene.shot import Keyframe, Shot
from ...domain.story.event import (
    Character,
    CharacterStatus,
    Event,
    EvidenceItem,
    EvidenceType,
)
from ...domain.story.graph import EdgeRelation, Plot, PlotMember, StoryEdge
from ...domain.time_range import TimeRange
from ...domain.transcript.transcript import Transcript, TranscriptSegment, Word

_NOW = "datetime('now')"


def _dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True)


class SqliteAnalysisRevisionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(self, revision: AnalysisRevision) -> None:
        self._connection.execute(
            "INSERT INTO analysis_revisions(id, episode_id, config_hash, source_sha256,"
            " status, schema_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, " + _NOW + ")",
            (
                revision.id,
                revision.episode_id,
                revision.config_hash,
                revision.source_sha256,
                revision.status.value,
                revision.schema_version,
            ),
        )

    def find_by_config(self, episode_id: str, config_hash: str) -> AnalysisRevision | None:
        row = self._connection.execute(
            "SELECT * FROM analysis_revisions WHERE episode_id = ? AND config_hash = ?",
            (episode_id, config_hash),
        ).fetchone()
        if row is None:
            return None
        return AnalysisRevision(
            id=row["id"],
            episode_id=row["episode_id"],
            config_hash=row["config_hash"],
            source_sha256=row["source_sha256"],
            status=RevisionStatus(row["status"]),
            schema_version=row["schema_version"],
        )

    def set_status(self, revision_id: str, status: RevisionStatus) -> None:
        self._connection.execute(
            "UPDATE analysis_revisions SET status = ? WHERE id = ?",
            (status.value, revision_id),
        )


class SqliteRecapRevisionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(self, revision: RecapRevision, profile_json: str) -> None:
        self._connection.execute(
            "INSERT INTO recap_revisions(id, analysis_revision_id, profile_hash,"
            " profile_json, status, created_at) VALUES (?, ?, ?, ?, ?, " + _NOW + ")",
            (
                revision.id,
                revision.analysis_revision_id,
                revision.profile_hash,
                profile_json,
                revision.status.value,
            ),
        )

    def find_by_profile(
        self, analysis_revision_id: str, profile_hash: str
    ) -> RecapRevision | None:
        row = self._connection.execute(
            "SELECT * FROM recap_revisions WHERE analysis_revision_id = ? AND profile_hash = ?",
            (analysis_revision_id, profile_hash),
        ).fetchone()
        if row is None:
            return None
        return RecapRevision(
            id=row["id"],
            analysis_revision_id=row["analysis_revision_id"],
            profile_hash=row["profile_hash"],
            status=RevisionStatus(row["status"]),
        )

    def ids_for_analysis(self, analysis_revision_id: str) -> tuple[str, ...]:
        return tuple(
            row["id"]
            for row in self._connection.execute(
                "SELECT id FROM recap_revisions WHERE analysis_revision_id = ?"
                " ORDER BY created_at, id",
                (analysis_revision_id,),
            )
        )

    def set_status(self, revision_id: str, status: RevisionStatus) -> None:
        self._connection.execute(
            "UPDATE recap_revisions SET status = ? WHERE id = ?",
            (status.value, revision_id),
        )


class SqliteStageCheckpointStore:
    """Durable per-stage completion records plus their artifact bindings."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def find(self, stage: str, scope_type: str, scope_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT * FROM stage_checkpoints WHERE stage = ? AND scope_type = ? AND scope_id = ?",
            (stage, scope_type, scope_id),
        ).fetchone()

    def artifact_rows(self, checkpoint_id: str) -> list[sqlite3.Row]:
        return list(
            self._connection.execute(
                "SELECT a.*, l.role AS role FROM stage_checkpoint_artifacts l"
                " JOIN artifacts a ON a.id = l.artifact_id"
                " WHERE l.checkpoint_id = ? ORDER BY l.role, a.path",
                (checkpoint_id,),
            )
        )

    def publish(
        self,
        *,
        checkpoint_id: str,
        stage: str,
        scope_type: str,
        scope_id: str,
        input_hash: str,
        output: dict[str, Any],
        artifacts: Sequence[tuple[str, str]],
    ) -> None:
        self._connection.execute(
            "INSERT INTO stage_checkpoints(id, stage, scope_type, scope_id, input_hash,"
            " state, output_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'SUCCEEDED', ?, " + _NOW + ")",
            (checkpoint_id, stage, scope_type, scope_id, input_hash, _dumps(output)),
        )
        for artifact_id, role in artifacts:
            self._connection.execute(
                "INSERT INTO stage_checkpoint_artifacts(checkpoint_id, artifact_id, role)"
                " VALUES (?, ?, ?)",
                (checkpoint_id, artifact_id, role),
            )

    def delete(self, stage: str, scope_type: str, scope_id: str) -> None:
        self._connection.execute(
            "DELETE FROM stage_checkpoints WHERE stage = ? AND scope_type = ? AND scope_id = ?",
            (stage, scope_type, scope_id),
        )


class SqliteTranscriptRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, analysis_revision_id: str, transcript: Transcript) -> None:
        for segment in transcript.segments:
            self._connection.execute(
                "INSERT INTO transcript_segments(id, analysis_revision_id, ordinal, start_ms,"
                " end_ms, speaker_id, text, words_json, confidence)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    segment.id,
                    analysis_revision_id,
                    segment.ordinal,
                    segment.range.start_ms,
                    segment.range.end_ms,
                    segment.speaker_id,
                    segment.text,
                    _dumps([word.to_dict() for word in segment.words]),
                    segment.confidence,
                ),
            )

    def delete(self, analysis_revision_id: str) -> None:
        self._connection.execute(
            "DELETE FROM transcript_segments WHERE analysis_revision_id = ?",
            (analysis_revision_id,),
        )

    def count(self, analysis_revision_id: str) -> int:
        return int(
            self._connection.execute(
                "SELECT COUNT(*) AS c FROM transcript_segments WHERE analysis_revision_id = ?",
                (analysis_revision_id,),
            ).fetchone()["c"]
        )

    def load(
        self,
        analysis_revision_id: str,
        *,
        language: str,
        duration_ms: int,
        audio_sha256: str,
        provenance: Provenance,
    ) -> Transcript:
        segments: list[TranscriptSegment] = []
        for row in self._connection.execute(
            "SELECT * FROM transcript_segments WHERE analysis_revision_id = ? ORDER BY ordinal",
            (analysis_revision_id,),
        ):
            words = tuple(
                Word(text=item["text"], range=TimeRange(item["start_ms"], item["end_ms"]))
                for item in json.loads(row["words_json"] or "[]")
            )
            segments.append(
                TranscriptSegment(
                    id=row["id"],
                    ordinal=row["ordinal"],
                    range=TimeRange(row["start_ms"], row["end_ms"]),
                    text=row["text"],
                    speaker_id=row["speaker_id"],
                    confidence=row["confidence"],
                    words=words,
                )
            )
        return Transcript(
            language=language,
            duration_ms=duration_ms,
            audio_sha256=audio_sha256,
            provenance=provenance,
            segments=tuple(segments),
        )


class SqliteShotRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, analysis_revision_id: str, shots: Iterable[Shot]) -> None:
        for shot in shots:
            self._connection.execute(
                "INSERT INTO shots(id, analysis_revision_id, ordinal, start_ms, end_ms,"
                " keyframes_json, excluded_reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    shot.id,
                    analysis_revision_id,
                    shot.ordinal,
                    shot.range.start_ms,
                    shot.range.end_ms,
                    _dumps([frame.to_dict() for frame in shot.keyframes]),
                    shot.excluded_reason,
                ),
            )

    def delete(self, analysis_revision_id: str) -> None:
        self._connection.execute(
            "DELETE FROM shots WHERE analysis_revision_id = ?", (analysis_revision_id,)
        )

    def load(self, analysis_revision_id: str) -> tuple[Shot, ...]:
        shots: list[Shot] = []
        for row in self._connection.execute(
            "SELECT * FROM shots WHERE analysis_revision_id = ? ORDER BY ordinal",
            (analysis_revision_id,),
        ):
            shots.append(
                Shot(
                    id=row["id"],
                    ordinal=row["ordinal"],
                    range=TimeRange(row["start_ms"], row["end_ms"]),
                    keyframes=tuple(
                        Keyframe.from_dict(item)
                        for item in json.loads(row["keyframes_json"])
                    ),
                    excluded_reason=row["excluded_reason"],
                )
            )
        return tuple(shots)

    def count(self, analysis_revision_id: str) -> int:
        return int(
            self._connection.execute(
                "SELECT COUNT(*) AS c FROM shots WHERE analysis_revision_id = ?",
                (analysis_revision_id,),
            ).fetchone()["c"]
        )


class SqliteCharacterRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, characters: Iterable[Character]) -> None:
        for character in characters:
            self._connection.execute(
                "INSERT INTO characters(id, analysis_revision_id, canonical_name, status,"
                " aliases_json, refs_json) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    character.id,
                    character.analysis_revision_id,
                    character.canonical_name,
                    character.status.value,
                    _dumps(list(character.aliases)),
                    _dumps(list(character.refs)),
                ),
            )

    def link_scene(
        self, scene_id: str, character_id: str, revision_id: str, confidence: float
    ) -> None:
        self._connection.execute(
            "INSERT OR IGNORE INTO scene_characters(scene_id, character_id,"
            " analysis_revision_id, confidence) VALUES (?, ?, ?, ?)",
            (scene_id, character_id, revision_id, confidence),
        )

    def delete(self, analysis_revision_id: str) -> None:
        self._connection.execute(
            "DELETE FROM characters WHERE analysis_revision_id = ?", (analysis_revision_id,)
        )

    def load(self, analysis_revision_id: str) -> tuple[Character, ...]:
        return tuple(
            Character(
                id=row["id"],
                analysis_revision_id=row["analysis_revision_id"],
                canonical_name=row["canonical_name"],
                status=CharacterStatus(row["status"]),
                aliases=tuple(json.loads(row["aliases_json"])),
                refs=tuple(json.loads(row["refs_json"])),
            )
            for row in self._connection.execute(
                "SELECT * FROM characters WHERE analysis_revision_id = ?"
                " ORDER BY canonical_name",
                (analysis_revision_id,),
            )
        )


class SqliteSceneRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, scenes: Iterable[Scene]) -> None:
        for scene in scenes:
            self._connection.execute(
                "INSERT INTO scenes(id, analysis_revision_id, ordinal, start_ms, end_ms,"
                " location, summary, confidence, schema_version, provenance_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    scene.id,
                    scene.analysis_revision_id,
                    scene.ordinal,
                    scene.range.start_ms,
                    scene.range.end_ms,
                    scene.location,
                    scene.summary,
                    scene.confidence,
                    scene.schema_version,
                    _dumps(scene.provenance.to_dict()),
                ),
            )
            for ordinal, shot_id in enumerate(scene.shot_ids):
                self._connection.execute(
                    "INSERT INTO scene_shots(scene_id, shot_id, analysis_revision_id, ordinal)"
                    " VALUES (?, ?, ?, ?)",
                    (scene.id, shot_id, scene.analysis_revision_id, ordinal),
                )

    def delete(self, analysis_revision_id: str) -> None:
        self._connection.execute(
            "DELETE FROM scenes WHERE analysis_revision_id = ?", (analysis_revision_id,)
        )

    def load(self, analysis_revision_id: str) -> tuple[Scene, ...]:
        scenes: list[Scene] = []
        for row in self._connection.execute(
            "SELECT * FROM scenes WHERE analysis_revision_id = ? ORDER BY ordinal",
            (analysis_revision_id,),
        ):
            shot_ids = tuple(
                item["shot_id"]
                for item in self._connection.execute(
                    "SELECT shot_id FROM scene_shots WHERE scene_id = ? ORDER BY ordinal",
                    (row["id"],),
                )
            )
            character_ids = tuple(
                item["character_id"]
                for item in self._connection.execute(
                    "SELECT character_id FROM scene_characters WHERE scene_id = ?"
                    " ORDER BY character_id",
                    (row["id"],),
                )
            )
            scenes.append(
                Scene(
                    id=row["id"],
                    analysis_revision_id=row["analysis_revision_id"],
                    ordinal=row["ordinal"],
                    range=TimeRange(row["start_ms"], row["end_ms"]),
                    shot_ids=shot_ids,
                    location=row["location"],
                    summary=row["summary"],
                    confidence=row["confidence"],
                    provenance=Provenance.from_dict(json.loads(row["provenance_json"])),
                    character_ids=character_ids,
                    schema_version=row["schema_version"],
                )
            )
        return tuple(scenes)

    def count(self, analysis_revision_id: str) -> int:
        return int(
            self._connection.execute(
                "SELECT COUNT(*) AS c FROM scenes WHERE analysis_revision_id = ?",
                (analysis_revision_id,),
            ).fetchone()["c"]
        )


class SqliteEventRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, events: Iterable[Event]) -> None:
        for event in events:
            self._connection.execute(
                "INSERT INTO events(id, scene_id, analysis_revision_id, ordinal, start_ms,"
                " end_ms, action, cause, consequence, importance, confidence)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.id,
                    event.scene_id,
                    event.analysis_revision_id,
                    event.ordinal,
                    event.range.start_ms,
                    event.range.end_ms,
                    event.action,
                    event.cause,
                    event.consequence,
                    event.importance,
                    event.confidence,
                ),
            )
            for character_id in event.character_ids:
                self._connection.execute(
                    "INSERT OR IGNORE INTO event_characters(event_id, character_id,"
                    " analysis_revision_id, role) VALUES (?, ?, ?, 'PARTICIPANT')",
                    (event.id, character_id, event.analysis_revision_id),
                )
            for item in event.evidence:
                self._connection.execute(
                    "INSERT INTO evidence_items(id, event_id, analysis_revision_id, type,"
                    " start_ms, end_ms, transcript_id, artifact_id, confidence)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        item.id,
                        item.event_id,
                        item.analysis_revision_id,
                        item.type.value,
                        item.range.start_ms,
                        item.range.end_ms,
                        item.transcript_id,
                        item.artifact_id,
                        item.confidence,
                    ),
                )

    def delete(self, analysis_revision_id: str) -> None:
        self._connection.execute(
            "DELETE FROM evidence_items WHERE analysis_revision_id = ?",
            (analysis_revision_id,),
        )
        self._connection.execute(
            "DELETE FROM events WHERE analysis_revision_id = ?", (analysis_revision_id,)
        )

    def load(self, analysis_revision_id: str) -> tuple[Event, ...]:
        events: list[Event] = []
        for row in self._connection.execute(
            "SELECT * FROM events WHERE analysis_revision_id = ? ORDER BY ordinal",
            (analysis_revision_id,),
        ):
            evidence = tuple(
                EvidenceItem(
                    id=item["id"],
                    event_id=item["event_id"],
                    analysis_revision_id=item["analysis_revision_id"],
                    type=EvidenceType(item["type"]),
                    range=TimeRange(item["start_ms"], item["end_ms"]),
                    confidence=item["confidence"],
                    transcript_id=item["transcript_id"],
                    artifact_id=item["artifact_id"],
                )
                for item in self._connection.execute(
                    "SELECT * FROM evidence_items WHERE event_id = ? ORDER BY start_ms, id",
                    (row["id"],),
                )
            )
            character_ids = tuple(
                item["character_id"]
                for item in self._connection.execute(
                    "SELECT character_id FROM event_characters WHERE event_id = ?"
                    " ORDER BY character_id",
                    (row["id"],),
                )
            )
            events.append(
                Event(
                    id=row["id"],
                    scene_id=row["scene_id"],
                    analysis_revision_id=row["analysis_revision_id"],
                    ordinal=row["ordinal"],
                    range=TimeRange(row["start_ms"], row["end_ms"]),
                    action=row["action"],
                    cause=row["cause"],
                    consequence=row["consequence"],
                    importance=row["importance"],
                    confidence=row["confidence"],
                    evidence=evidence,
                    character_ids=character_ids,
                )
            )
        return tuple(events)

    def counts(self, analysis_revision_id: str) -> tuple[int, int]:
        events = self._connection.execute(
            "SELECT COUNT(*) AS c FROM events WHERE analysis_revision_id = ?",
            (analysis_revision_id,),
        ).fetchone()["c"]
        evidence = self._connection.execute(
            "SELECT COUNT(*) AS c FROM evidence_items WHERE analysis_revision_id = ?",
            (analysis_revision_id,),
        ).fetchone()["c"]
        return int(events), int(evidence)


class SqliteStoryRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, plots: Iterable[Plot], edges: Iterable[StoryEdge]) -> None:
        for plot in plots:
            self._connection.execute(
                "INSERT INTO plots(id, analysis_revision_id, ordinal, title, summary,"
                " importance) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    plot.id,
                    plot.analysis_revision_id,
                    plot.ordinal,
                    plot.title,
                    plot.summary,
                    plot.importance,
                ),
            )
            for member in plot.members:
                self._connection.execute(
                    "INSERT INTO plot_events(plot_id, event_id, analysis_revision_id, ordinal,"
                    " membership_score) VALUES (?, ?, ?, ?, ?)",
                    (
                        plot.id,
                        member.event_id,
                        plot.analysis_revision_id,
                        member.ordinal,
                        member.membership_score,
                    ),
                )
        for edge in edges:
            self._connection.execute(
                "INSERT INTO story_edges(id, analysis_revision_id, from_event_id, to_event_id,"
                " relation, confidence) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    edge.id,
                    edge.analysis_revision_id,
                    edge.from_event_id,
                    edge.to_event_id,
                    edge.relation.value,
                    edge.confidence,
                ),
            )

    def delete(self, analysis_revision_id: str) -> None:
        self._connection.execute(
            "DELETE FROM story_edges WHERE analysis_revision_id = ?", (analysis_revision_id,)
        )
        self._connection.execute(
            "DELETE FROM plots WHERE analysis_revision_id = ?", (analysis_revision_id,)
        )

    def load_plots(self, analysis_revision_id: str) -> tuple[Plot, ...]:
        plots: list[Plot] = []
        for row in self._connection.execute(
            "SELECT * FROM plots WHERE analysis_revision_id = ? ORDER BY ordinal",
            (analysis_revision_id,),
        ):
            members = tuple(
                PlotMember(
                    event_id=item["event_id"],
                    ordinal=item["ordinal"],
                    membership_score=item["membership_score"],
                )
                for item in self._connection.execute(
                    "SELECT * FROM plot_events WHERE plot_id = ? ORDER BY ordinal",
                    (row["id"],),
                )
            )
            plots.append(
                Plot(
                    id=row["id"],
                    analysis_revision_id=row["analysis_revision_id"],
                    ordinal=row["ordinal"],
                    title=row["title"],
                    summary=row["summary"],
                    importance=row["importance"],
                    members=members,
                )
            )
        return tuple(plots)

    def load_edges(self, analysis_revision_id: str) -> tuple[StoryEdge, ...]:
        return tuple(
            StoryEdge(
                id=row["id"],
                analysis_revision_id=row["analysis_revision_id"],
                from_event_id=row["from_event_id"],
                to_event_id=row["to_event_id"],
                relation=EdgeRelation(row["relation"]),
                confidence=row["confidence"],
            )
            for row in self._connection.execute(
                "SELECT * FROM story_edges WHERE analysis_revision_id = ? ORDER BY id",
                (analysis_revision_id,),
            )
        )

    def counts(self, analysis_revision_id: str) -> tuple[int, int]:
        plots = self._connection.execute(
            "SELECT COUNT(*) AS c FROM plots WHERE analysis_revision_id = ?",
            (analysis_revision_id,),
        ).fetchone()["c"]
        edges = self._connection.execute(
            "SELECT COUNT(*) AS c FROM story_edges WHERE analysis_revision_id = ?",
            (analysis_revision_id,),
        ).fetchone()["c"]
        return int(plots), int(edges)


class SqliteRecapPlanRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, plan: RecapPlan) -> None:
        self._connection.execute(
            "INSERT INTO recap_plans(id, recap_revision_id, selected_plots_json, estimated_ms,"
            " policy_json, schema_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, " + _NOW + ")",
            (
                plan.id,
                plan.recap_revision_id,
                _dumps(list(plan.selected_plot_ids)),
                plan.estimated_duration_ms,
                _dumps(
                    {
                        "profile": plan.profile.to_dict(),
                        "budget": plan.budget.to_dict(),
                        "must_have_omissions": [dict(e) for e in plan.must_have_omissions],
                        "provenance": plan.provenance.to_dict(),
                    }
                ),
                plan.schema_version,
            ),
        )
        for planned in plan.ordered_events:
            self._connection.execute(
                "INSERT INTO recap_plan_events(plan_id, event_id, ordinal, budget_class,"
                " decision_reason) VALUES (?, ?, ?, ?, ?)",
                (
                    plan.id,
                    planned.event_id,
                    planned.ordinal,
                    planned.budget_class.value,
                    planned.decision_reason,
                ),
            )

    def delete(self, recap_revision_id: str) -> None:
        self._connection.execute(
            "DELETE FROM recap_plans WHERE recap_revision_id = ?", (recap_revision_id,)
        )

    def find(self, recap_revision_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT * FROM recap_plans WHERE recap_revision_id = ?", (recap_revision_id,)
        ).fetchone()

    def event_count(self, plan_id: str) -> int:
        return int(
            self._connection.execute(
                "SELECT COUNT(*) AS c FROM recap_plan_events WHERE plan_id = ?", (plan_id,)
            ).fetchone()["c"]
        )
