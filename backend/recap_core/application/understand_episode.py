"""Dependency-aware orchestration from an imported Episode to a basic RecapPlan.

The pipeline is a DAG of durable stages. A stage is skipped only when its
checkpoint agrees with the recomputed input hash, with the bytes of every artifact
it published, and with the rows it claims to have written; otherwise the stage and
exactly its DAG descendants are invalidated and rebuilt. Publication of files is
atomic and publication of rows is transactional, so a downstream stage can never
observe a partially written predecessor.

`AnalysisConfig` intentionally has no recap/profile input, which is what makes
"analyze once, generate many" hold: a profile change can only create a new recap
revision and can never invalidate analysis artifacts.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable

from ..domain.analysis.revision import (
    AnalysisConfig,
    AnalysisRevision,
    RecapRevision,
    RevisionStatus,
)
from ..domain.errors import (
    CheckpointIntegrityError,
    ProjectNotFoundError,
    SchemaValidationError,
    SourceIdentityMismatchError,
    SourceMissingError,
    StageDependencyError,
    ValidationError,
)
from ..domain.identity import hash_bytes, hash_file, new_id
from ..domain.project import Episode
from ..domain.provenance import Provenance
from ..domain.recap.planner import PLANNER_VERSION, build_plan
from ..domain.recap.profile import RecapProfile
from ..domain.scene.grouping import GROUPER_VERSION, group_shots_into_scenes
from ..domain.scene.scene import Scene, ScenePackage
from ..domain.scene.shot import Keyframe, Shot
from ..domain.story.characters import (
    UNKNOWN_CHARACTER_NAME,
    canonical_key,
    resolve_characters,
)
from ..domain.story.event import Event, EvidenceItem, EvidenceType
from ..domain.story.graph import Plot, PlotMember, StoryEdge, StoryGraph
from ..domain.time_range import TimeRange
from ..domain.transcript.transcript import Transcript, TranscriptSegment, Word
from ..infrastructure.database.analysis_repositories import (
    SqliteAnalysisRevisionRepository,
    SqliteCharacterRepository,
    SqliteEventRepository,
    SqliteRecapPlanRepository,
    SqliteRecapRevisionRepository,
    SqliteSceneRepository,
    SqliteShotRepository,
    SqliteStageCheckpointStore,
    SqliteStoryRepository,
    SqliteTranscriptRepository,
)
from ..infrastructure.database.repositories import (
    SqliteArtifactRepository,
    SqliteEpisodeRepository,
    SqliteProjectRepository,
)
from ..infrastructure.filesystem.project_layout import ProjectLayout
from ..ports.media_processor import MediaProcessor
from ..ports.scene_analysis import SceneAnalysisProvider, ensure_scene_label
from ..ports.story_reasoning import (
    StoryReasoningProvider,
    ensure_event_drafts,
    ensure_story_reasoning,
)
from ..ports.stt import STTProvider, ensure_transcript_dto
from ..schemas.validator import SchemaRegistry


class Stage(str, Enum):
    PREPROCESS = "PREPROCESS"
    TRANSCRIBE = "TRANSCRIBE"
    DETECT_SHOTS = "DETECT_SHOTS"
    BUILD_SCENES = "BUILD_SCENES"
    EXTRACT_EVENTS = "EXTRACT_EVENTS"
    BUILD_STORY_GRAPH = "BUILD_STORY_GRAPH"
    PLAN_RECAP = "PLAN_RECAP"


#: A topological order of the stage DAG.
STAGE_ORDER: tuple[Stage, ...] = (
    Stage.PREPROCESS,
    Stage.TRANSCRIBE,
    Stage.DETECT_SHOTS,
    Stage.BUILD_SCENES,
    Stage.EXTRACT_EVENTS,
    Stage.BUILD_STORY_GRAPH,
    Stage.PLAN_RECAP,
)

DEPENDENCIES: dict[Stage, tuple[Stage, ...]] = {
    Stage.PREPROCESS: (),
    Stage.TRANSCRIBE: (Stage.PREPROCESS,),
    Stage.DETECT_SHOTS: (Stage.PREPROCESS,),
    Stage.BUILD_SCENES: (Stage.DETECT_SHOTS, Stage.TRANSCRIBE),
    Stage.EXTRACT_EVENTS: (Stage.BUILD_SCENES, Stage.TRANSCRIBE),
    Stage.BUILD_STORY_GRAPH: (Stage.EXTRACT_EVENTS,),
    Stage.PLAN_RECAP: (Stage.BUILD_STORY_GRAPH,),
}

SCOPE_ANALYSIS = "ANALYSIS_REVISION"
SCOPE_RECAP = "RECAP_REVISION"

ARTIFACT_PROXY = "media_proxy"
ARTIFACT_STT_AUDIO = "stt_audio"
ARTIFACT_TRANSCRIPT = "transcript"
ARTIFACT_SHOTS = "shots"
ARTIFACT_KEYFRAME = "keyframe"
ARTIFACT_SCENES = "scenes"
ARTIFACT_EVENTS = "events"
ARTIFACT_STORY_GRAPH = "story_graph"
ARTIFACT_RECAP_PLAN = "recap_plan"

DEFAULT_STT_LANGUAGE = "vi"

#: Published schema for every artifact kind that carries a stage document.
ARTIFACT_SCHEMAS: dict[str, str] = {
    ARTIFACT_TRANSCRIPT: "transcript",
    ARTIFACT_SHOTS: "shots",
    ARTIFACT_SCENES: "scenes",
    ARTIFACT_EVENTS: "events",
    ARTIFACT_STORY_GRAPH: "story-graph",
    ARTIFACT_RECAP_PLAN: "recap-plan",
}

#: Artifact kinds each stage owns and may therefore replace when it rebuilds.
STAGE_ARTIFACT_KINDS: dict[Stage, tuple[str, ...]] = {
    Stage.PREPROCESS: (ARTIFACT_PROXY, ARTIFACT_STT_AUDIO),
    Stage.TRANSCRIBE: (ARTIFACT_TRANSCRIPT,),
    Stage.DETECT_SHOTS: (ARTIFACT_SHOTS, ARTIFACT_KEYFRAME),
    Stage.BUILD_SCENES: (ARTIFACT_SCENES,),
    Stage.EXTRACT_EVENTS: (ARTIFACT_EVENTS,),
    Stage.BUILD_STORY_GRAPH: (ARTIFACT_STORY_GRAPH,),
    Stage.PLAN_RECAP: (ARTIFACT_RECAP_PLAN,),
}


def descendants(stage: Stage) -> tuple[Stage, ...]:
    """DAG descendants of `stage`, deepest first."""
    found: set[Stage] = set()
    changed = True
    while changed:
        changed = False
        for candidate, parents in DEPENDENCIES.items():
            if candidate in found:
                continue
            if stage in parents or found & set(parents):
                found.add(candidate)
                changed = True
    return tuple(item for item in reversed(STAGE_ORDER) if item in found)


def _dumps(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")


@dataclass
class _PublishedArtifact:
    """One artifact row a stage is about to publish, plus its checkpoint role."""

    kind: str
    role: str
    path: Path
    sha256: str
    size: int
    producer_version: str
    cache_key: str
    id: str = field(default_factory=new_id)

    def summary(self) -> dict[str, Any]:
        return {
            "artifact_id": self.id,
            "kind": self.kind,
            "path": str(self.path),
            "sha256": self.sha256,
            "size": self.size,
        }


@dataclass
class _StageOutcome:
    output: dict[str, Any]
    artifacts: list[_PublishedArtifact]
    rows: Callable[[], None]


@dataclass
class _Context:
    episode: Episode
    source: Path
    layout: ProjectLayout
    config: AnalysisConfig
    revision: AnalysisRevision
    recap_revision: RecapRevision
    profile: RecapProfile
    outputs: dict[Stage, dict[str, Any]] = field(default_factory=dict)

    def scope(self, stage: Stage) -> tuple[str, str]:
        if stage is Stage.PLAN_RECAP:
            return SCOPE_RECAP, self.recap_revision.id
        return SCOPE_ANALYSIS, self.revision.id


@dataclass(frozen=True)
class UnderstandResult:
    """What one orchestration pass produced or reused."""

    episode_id: str
    analysis_revision_id: str
    recap_revision_id: str
    plan_id: str
    plan_path: Path
    executed: tuple[str, ...]
    reused: tuple[str, ...]
    artifacts: dict[str, dict[str, Any]]


class UnderstandEpisodeUseCase:
    """Run the episode-understanding DAG for one episode and recap profile."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        media_processor: MediaProcessor,
        stt_provider: STTProvider,
        scene_provider: SceneAnalysisProvider,
        story_provider: StoryReasoningProvider,
        stt_language: str = DEFAULT_STT_LANGUAGE,
        schema_registry: SchemaRegistry | None = None,
        before_publish: Callable[[str], None] | None = None,
    ) -> None:
        self._connection = connection
        self._processor = media_processor
        self._stt = stt_provider
        self._scene = scene_provider
        self._story = story_provider
        self._stt_language = stt_language
        self._schemas = schema_registry or SchemaRegistry()
        self._before_publish = before_publish

        self._projects = SqliteProjectRepository(connection)
        self._episodes = SqliteEpisodeRepository(connection)
        self._artifacts = SqliteArtifactRepository(connection)
        self._revisions = SqliteAnalysisRevisionRepository(connection)
        self._recap_revisions = SqliteRecapRevisionRepository(connection)
        self._checkpoints = SqliteStageCheckpointStore(connection)
        self._transcripts = SqliteTranscriptRepository(connection)
        self._shots = SqliteShotRepository(connection)
        self._scenes = SqliteSceneRepository(connection)
        self._characters = SqliteCharacterRepository(connection)
        self._events = SqliteEventRepository(connection)
        self._story_rows = SqliteStoryRepository(connection)
        self._plans = SqliteRecapPlanRepository(connection)

    # -- entry point ---------------------------------------------------------

    def execute(self, *, episode_id: str, profile: RecapProfile | None = None) -> UnderstandResult:
        profile = profile or RecapProfile()
        episode = self._episodes.get(episode_id)
        if episode is None:
            raise ValidationError(f"episode not persisted: {episode_id}")
        project = self._projects.get(episode.project_id)
        if project is None:
            raise ProjectNotFoundError(f"project not persisted: {episode.project_id}")

        source = Path(episode.source_path)
        if not source.is_file():
            raise SourceMissingError(f"episode source not found: {source}")
        if hash_file(source) != episode.source_sha256:
            raise SourceIdentityMismatchError(
                "source bytes changed for {0}: recorded {1}".format(
                    source, episode.source_sha256
                )
            )

        config = self._analysis_config()
        revision = self._ensure_analysis_revision(episode, config)
        recap_revision = self._ensure_recap_revision(revision, profile)
        context = _Context(
            episode=episode,
            source=source,
            layout=ProjectLayout(project.root_path).create(),
            config=config,
            revision=revision,
            recap_revision=recap_revision,
            profile=profile,
        )

        executed: list[str] = []
        reused: list[str] = []
        for stage in STAGE_ORDER:
            if self._ensure_stage(stage, context):
                executed.append(stage.value)
            else:
                reused.append(stage.value)

        self._revisions.set_status(revision.id, RevisionStatus.COMPLETE)
        self._recap_revisions.set_status(recap_revision.id, RevisionStatus.COMPLETE)
        plan_output = context.outputs[Stage.PLAN_RECAP]
        return UnderstandResult(
            episode_id=episode.id,
            analysis_revision_id=revision.id,
            recap_revision_id=recap_revision.id,
            plan_id=plan_output["plan_id"],
            plan_path=Path(plan_output["artifacts"]["plan"]["path"]),
            executed=tuple(executed),
            reused=tuple(reused),
            artifacts={
                role: summary
                for output in context.outputs.values()
                for role, summary in output.get("artifacts", {}).items()
            },
        )

    # -- revisions -----------------------------------------------------------

    def _analysis_config(self) -> AnalysisConfig:
        """Every causally relevant analysis version, and nothing recap-specific."""
        return AnalysisConfig(
            proxy_version=self._processor.proxy_version,
            audio_version=self._processor.audio_version,
            shot_version=self._processor.shot_version,
            grouper_version=GROUPER_VERSION,
            stt_provider=self._stt.name,
            stt_version=self._stt.version,
            stt_model=self._stt.model,
            stt_language=self._stt_language,
            scene_provider=self._scene.name,
            scene_version=self._scene.version,
            story_provider=self._story.name,
            story_version=self._story.version,
        )

    def _ensure_analysis_revision(
        self, episode: Episode, config: AnalysisConfig
    ) -> AnalysisRevision:
        config_hash = config.config_hash(episode.source_sha256)
        existing = self._revisions.find_by_config(episode.id, config_hash)
        if existing is not None:
            if existing.source_sha256 != episode.source_sha256:
                raise SourceIdentityMismatchError(
                    f"analysis revision {existing.id} is bound to other source bytes"
                )
            return existing
        revision = AnalysisRevision(
            id=new_id(),
            episode_id=episode.id,
            config_hash=config_hash,
            source_sha256=episode.source_sha256,
        )
        self._in_transaction(lambda: self._revisions.add(revision))
        return revision

    def _ensure_recap_revision(
        self, revision: AnalysisRevision, profile: RecapProfile
    ) -> RecapRevision:
        profile_hash = profile.profile_hash()
        existing = self._recap_revisions.find_by_profile(revision.id, profile_hash)
        if existing is not None:
            return existing
        recap_revision = RecapRevision(
            id=new_id(), analysis_revision_id=revision.id, profile_hash=profile_hash
        )
        payload = json.dumps(profile.to_dict(), sort_keys=True)
        self._in_transaction(lambda: self._recap_revisions.add(recap_revision, payload))
        return recap_revision

    # -- stage driver --------------------------------------------------------

    def _ensure_stage(self, stage: Stage, context: _Context) -> bool:
        """Run `stage` unless its checkpoint is causally valid. Returns True if run."""
        input_hash = self._input_hash(stage, context)
        scope_type, scope_id = context.scope(stage)
        checkpoint = self._checkpoints.find(stage.value, scope_type, scope_id)
        if checkpoint is not None and self._checkpoint_valid(
            checkpoint, stage, context, input_hash
        ):
            context.outputs[stage] = json.loads(checkpoint["output_json"])
            return False

        self._invalidate(stage, context)
        outcome = self._run(stage, context, input_hash)
        self._publish(stage, context, input_hash, outcome)
        context.outputs[stage] = outcome.output
        return True

    def _checkpoint_valid(
        self, checkpoint: sqlite3.Row, stage: Stage, context: _Context, input_hash: str
    ) -> bool:
        """A checkpoint is evidence only when identity, bytes and rows all agree."""
        if checkpoint["input_hash"] != input_hash:
            return False
        try:
            output = json.loads(checkpoint["output_json"])
        except ValueError:
            return False
        if not isinstance(output, dict):
            return False

        rows = self._checkpoints.artifact_rows(checkpoint["id"])
        declared = output.get("artifacts")
        if not isinstance(declared, dict) or len(rows) != int(output.get("artifact_count", -1)):
            return False
        for row in rows:
            path = Path(row["path"])
            if not path.is_file() or path.stat().st_size != int(row["size"]):
                return False
            if hash_file(path) != row["sha256"]:
                return False
            if not self._document_still_valid(row):
                return False
        published = {row["id"]: row for row in rows}
        for summary in declared.values():
            row = published.get(summary.get("artifact_id"))
            if row is None:
                return False
            if row["sha256"] != summary.get("sha256") or int(row["size"]) != summary.get("size"):
                return False
        return output.get("counts") == self._row_counts(stage, context)

    def _document_still_valid(self, row: sqlite3.Row) -> bool:
        """A stage document is reusable only while it still satisfies its schema."""
        schema = ARTIFACT_SCHEMAS.get(row["kind"])
        if schema is None:
            return True
        try:
            self._schemas.validate(schema, json.loads(Path(row["path"]).read_bytes()))
        except (SchemaValidationError, ValueError, UnicodeDecodeError):
            return False
        return True

    def _row_counts(self, stage: Stage, context: _Context) -> dict[str, int]:
        """Authoritative persisted-row counts a checkpoint must agree with."""
        revision_id = context.revision.id
        if stage is Stage.TRANSCRIBE:
            return {"transcript_segments": self._transcripts.count(revision_id)}
        if stage is Stage.DETECT_SHOTS:
            return {"shots": self._shots.count(revision_id)}
        if stage is Stage.BUILD_SCENES:
            return {
                "scenes": self._scenes.count(revision_id),
                "characters": len(self._characters.load(revision_id)),
            }
        if stage is Stage.EXTRACT_EVENTS:
            events, evidence = self._events.counts(revision_id)
            return {"events": events, "evidence_items": evidence}
        if stage is Stage.BUILD_STORY_GRAPH:
            plots, edges = self._story_rows.counts(revision_id)
            return {"plots": plots, "story_edges": edges}
        if stage is Stage.PLAN_RECAP:
            plan = self._plans.find(context.recap_revision.id)
            if plan is None:
                return {"recap_plans": 0, "recap_plan_events": 0}
            return {
                "recap_plans": 1,
                "recap_plan_events": self._plans.event_count(plan["id"]),
            }
        return {}

    def _invalidate(self, stage: Stage, context: _Context) -> None:
        """Drop `stage` and exactly its DAG descendants, deepest first.

        As a descendant, `PLAN_RECAP` is invalidated for every recap revision of
        the analysis revision, because changed analysis invalidates every plan
        derived from it. As the stage being rebuilt it is invalidated only for the
        active recap revision, so planning one profile never destroys another.
        """
        for target in descendants(stage):
            self._in_transaction(
                lambda t=target: self._invalidate_stage(t, context, every_recap_revision=True)
            )
        self._in_transaction(
            lambda: self._invalidate_stage(stage, context, every_recap_revision=False)
        )

    def _invalidate_stage(
        self, stage: Stage, context: _Context, *, every_recap_revision: bool
    ) -> None:
        revision_id = context.revision.id
        scopes: Iterable[tuple[str, str]]
        if stage is Stage.PLAN_RECAP:
            recap_ids = (
                self._recap_revisions.ids_for_analysis(revision_id)
                if every_recap_revision
                else (context.recap_revision.id,)
            )
            scopes = [(SCOPE_RECAP, recap_id) for recap_id in recap_ids]
        else:
            scopes = [(SCOPE_ANALYSIS, revision_id)]

        for scope_type, scope_id in scopes:
            checkpoint = self._checkpoints.find(stage.value, scope_type, scope_id)
            if stage is Stage.PLAN_RECAP:
                self._plans.delete(scope_id)
            elif stage is Stage.BUILD_STORY_GRAPH:
                self._story_rows.delete(revision_id)
            elif stage is Stage.EXTRACT_EVENTS:
                self._events.delete(revision_id)
            elif stage is Stage.BUILD_SCENES:
                self._scenes.delete(revision_id)
                self._characters.delete(revision_id)
            elif stage is Stage.DETECT_SHOTS:
                self._shots.delete(revision_id)
            elif stage is Stage.TRANSCRIBE:
                self._transcripts.delete(revision_id)
            if checkpoint is not None:
                self._checkpoints.delete(stage.value, scope_type, scope_id)
            # Dropping the checkpoint unbinds the artifacts it vouched for, so the
            # sweep also collects rows an earlier tampering or partial state left
            # behind. Deepest-first ordering means every ON DELETE RESTRICT
            # reference is already gone; a survivor raises instead of orphaning
            # evidence.
            self._artifacts.delete_unlinked(
                context.episode.id, STAGE_ARTIFACT_KINDS[stage]
            )

    def _publish(
        self, stage: Stage, context: _Context, input_hash: str, outcome: _StageOutcome
    ) -> None:
        """Publish artifacts, rows and the checkpoint in one transaction."""
        if self._before_publish is not None:
            self._before_publish(stage.value)
        for artifact in outcome.artifacts:
            if not artifact.path.is_file() or artifact.path.stat().st_size != artifact.size:
                raise CheckpointIntegrityError(
                    f"refusing to publish {stage.value}: artifact {artifact.path} is unusable"
                )
            if hash_file(artifact.path) != artifact.sha256:
                raise CheckpointIntegrityError(
                    f"refusing to publish {stage.value}: artifact {artifact.path} drifted"
                )
        self._assert_source_unchanged(context)

        checkpoint_id = new_id()
        scope_type, scope_id = context.scope(stage)
        predecessor_ids = self._predecessor_artifact_ids(stage, context)

        def write() -> None:
            for artifact in outcome.artifacts:
                self._artifacts.publish(
                    artifact_id=artifact.id,
                    episode_id=context.episode.id,
                    kind=artifact.kind,
                    path=str(artifact.path),
                    sha256=artifact.sha256,
                    size=artifact.size,
                    producer_version=artifact.producer_version,
                    cache_key=artifact.cache_key,
                )
                for dependency_id in predecessor_ids:
                    self._artifacts.add_dependency(artifact.id, dependency_id)
            outcome.rows()
            self._checkpoints.publish(
                checkpoint_id=checkpoint_id,
                stage=stage.value,
                scope_type=scope_type,
                scope_id=scope_id,
                input_hash=input_hash,
                output=outcome.output,
                artifacts=[(a.id, a.role) for a in outcome.artifacts],
            )

        self._in_transaction(write)

        actual = self._row_counts(stage, context)
        if outcome.output.get("counts") != actual:
            raise CheckpointIntegrityError(
                "{0} published {1} but the database holds {2}".format(
                    stage.value, outcome.output.get("counts"), actual
                )
            )

    def _predecessor_artifact_ids(self, stage: Stage, context: _Context) -> tuple[str, ...]:
        ids: list[str] = []
        for parent in DEPENDENCIES[stage]:
            output = context.outputs.get(parent) or {}
            for summary in output.get("artifacts", {}).values():
                artifact_id = summary.get("artifact_id")
                if isinstance(artifact_id, str):
                    ids.append(artifact_id)
        return tuple(dict.fromkeys(ids))

    def _assert_source_unchanged(self, context: _Context) -> None:
        """Revalidate source identity at every publication boundary."""
        if not context.source.is_file():
            raise SourceMissingError(
                f"refusing to publish: source file disappeared: {context.source}"
            )
        if hash_file(context.source) != context.episode.source_sha256:
            raise SourceIdentityMismatchError(
                "source bytes changed before publication for {0}: recorded {1}".format(
                    context.source, context.episode.source_sha256
                )
            )

    def _in_transaction(self, action: Callable[[], None]) -> None:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            action()
            self._connection.execute("COMMIT")
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise

    # -- provenance and cache keys -------------------------------------------

    def _provenance(self, stage: Stage, context: _Context) -> Provenance:
        source = context.episode.source_sha256
        if stage is Stage.PREPROCESS:
            return Provenance(
                source_sha256=source,
                producer="media-preprocess",
                producer_version="{0}+{1}".format(
                    self._processor.proxy_version, self._processor.audio_version
                ),
            )
        if stage is Stage.TRANSCRIBE:
            return Provenance(
                source_sha256=source,
                producer="stt",
                producer_version=self._stt.version,
                model=self._stt.model,
                provider=self._stt.name,
            )
        if stage is Stage.DETECT_SHOTS:
            return Provenance(
                source_sha256=source,
                producer="shot-detection",
                producer_version=self._processor.shot_version,
            )
        if stage is Stage.BUILD_SCENES:
            return Provenance(
                source_sha256=source,
                producer="scene-analysis",
                producer_version="{0}+{1}".format(GROUPER_VERSION, self._scene.version),
                model=self._scene.model,
                provider=self._scene.name,
            )
        if stage is Stage.EXTRACT_EVENTS:
            return Provenance(
                source_sha256=source,
                producer="event-extraction",
                producer_version=self._story.version,
                model=self._story.model,
                provider=self._story.name,
            )
        if stage is Stage.BUILD_STORY_GRAPH:
            return Provenance(
                source_sha256=source,
                producer="story-reasoning",
                producer_version=self._story.version,
                model=self._story.model,
                provider=self._story.name,
            )
        return Provenance(
            source_sha256=source,
            producer="recap-planner",
            producer_version=PLANNER_VERSION,
        )

    def _input_hash(self, stage: Stage, context: _Context) -> str:
        """Fold provenance, scope and every predecessor artifact identity together."""
        parts: list[str] = [context.revision.id, context.revision.config_hash]
        if stage is Stage.PLAN_RECAP:
            parts.extend([context.recap_revision.id, context.recap_revision.profile_hash])
        for parent in DEPENDENCIES[stage]:
            output = context.outputs.get(parent)
            if output is None:
                raise StageDependencyError(
                    f"{stage.value} cannot run before {parent.value} produced output"
                )
            for role in sorted(output.get("artifacts", {})):
                parts.append(f"{parent.value}:{role}:{output['artifacts'][role]['sha256']}")
        return self._provenance(stage, context).cache_key(*parts)

    # -- stage bodies --------------------------------------------------------

    def _run(self, stage: Stage, context: _Context, input_hash: str) -> _StageOutcome:
        runners: dict[Stage, Callable[[_Context, str], _StageOutcome]] = {
            Stage.PREPROCESS: self._run_preprocess,
            Stage.TRANSCRIBE: self._run_transcribe,
            Stage.DETECT_SHOTS: self._run_detect_shots,
            Stage.BUILD_SCENES: self._run_build_scenes,
            Stage.EXTRACT_EVENTS: self._run_extract_events,
            Stage.BUILD_STORY_GRAPH: self._run_build_story_graph,
            Stage.PLAN_RECAP: self._run_plan_recap,
        }
        return runners[stage](context, input_hash)

    def _run_preprocess(self, context: _Context, input_hash: str) -> _StageOutcome:
        revision = context.revision.id
        proxy_path = context.layout.resolve(f"proxy/{revision}.720p.mp4")
        audio_path = context.layout.resolve(f"proxy/{revision}.stt16k.wav")
        proxy = self._processor.make_proxy(context.source, proxy_path, context.episode.media)
        audio = self._processor.make_stt_audio(context.source, audio_path)

        artifacts = [
            self._artifact(
                ARTIFACT_PROXY, "proxy", proxy.path, self._processor.proxy_version, input_hash
            ),
            self._artifact(
                ARTIFACT_STT_AUDIO,
                "stt_audio",
                audio.path,
                self._processor.audio_version,
                input_hash,
            ),
        ]
        output = {
            "artifacts": {a.role: a.summary() for a in artifacts},
            "artifact_count": len(artifacts),
            "counts": {},
            "proxy": {
                "width": proxy.width,
                "height": proxy.height,
                "duration_ms": proxy.duration_ms,
            },
            "audio": {
                "sample_rate": audio.sample_rate,
                "channels": audio.channels,
                "duration_ms": audio.duration_ms,
            },
            "provenance": self._provenance(Stage.PREPROCESS, context).to_dict(),
        }
        return _StageOutcome(output=output, artifacts=artifacts, rows=lambda: None)

    def _run_transcribe(self, context: _Context, input_hash: str) -> _StageOutcome:
        audio = self._artifact_path(context, Stage.PREPROCESS, "stt_audio")
        audio_sha256 = context.outputs[Stage.PREPROCESS]["artifacts"]["stt_audio"]["sha256"]
        provenance = self._provenance(Stage.TRANSCRIBE, context)

        dto = ensure_transcript_dto(
            self._stt.transcribe(audio, self._stt_language, {"word_timestamps": True})
        )
        duration_ms = context.episode.duration_ms
        segments = tuple(
            TranscriptSegment(
                id=new_id(),
                ordinal=index,
                range=TimeRange(item.start_ms, min(item.end_ms, duration_ms)),
                text=item.text,
                speaker_id=item.speaker_id or "SPEAKER_UNKNOWN",
                confidence=item.confidence,
                words=tuple(
                    Word(
                        text=word["text"],
                        range=TimeRange(word["start_ms"], min(word["end_ms"], duration_ms)),
                    )
                    for word in item.words
                    if word["start_ms"] < duration_ms
                ),
            )
            for index, item in enumerate(dto.segments)
            if item.start_ms < duration_ms
        )
        transcript = Transcript(
            language=dto.language,
            duration_ms=duration_ms,
            audio_sha256=audio_sha256,
            provenance=provenance,
            segments=segments,
        )
        document = transcript.to_dict()
        self._schemas.validate("transcript", document)
        artifact = self._write_json(
            context,
            f"transcript/{context.revision.id}.transcript.json",
            document,
            kind=ARTIFACT_TRANSCRIPT,
            role="transcript",
            producer_version=provenance.producer_version,
            cache_key=input_hash,
        )
        output = {
            "artifacts": {artifact.role: artifact.summary()},
            "artifact_count": 1,
            "counts": {"transcript_segments": len(segments)},
            "language": transcript.language,
            "provenance": provenance.to_dict(),
        }
        return _StageOutcome(
            output=output,
            artifacts=[artifact],
            rows=lambda: self._transcripts.insert(context.revision.id, transcript),
        )

    def _run_detect_shots(self, context: _Context, input_hash: str) -> _StageOutcome:
        proxy = self._artifact_path(context, Stage.PREPROCESS, "proxy")
        provenance = self._provenance(Stage.DETECT_SHOTS, context)
        keyframe_dir = context.layout.resolve("scenes/keyframes")
        results = self._processor.detect_shots(
            proxy, context.episode.duration_ms, keyframe_dir, context.revision.id
        )

        shots: list[Shot] = []
        keyframe_artifacts: list[_PublishedArtifact] = []
        for index, result in enumerate(results):
            frames: list[Keyframe] = []
            for frame in result.keyframes:
                path = context.layout.resolve(frame.path)
                digest, size = self._file_identity(path)
                frames.append(
                    Keyframe(
                        timestamp_ms=frame.timestamp_ms,
                        path=str(path),
                        sha256=digest,
                        size=size,
                    )
                )
                keyframe_artifacts.append(
                    _PublishedArtifact(
                        kind=ARTIFACT_KEYFRAME,
                        role=f"keyframe:{frame.timestamp_ms}",
                        path=path,
                        sha256=digest,
                        size=size,
                        producer_version=self._processor.shot_version,
                        cache_key=self._sub_key(input_hash, str(path)),
                    )
                )
            shots.append(
                Shot(
                    id=new_id(),
                    ordinal=index,
                    range=TimeRange(result.start_ms, result.end_ms),
                    keyframes=tuple(frames),
                )
            )
        if not shots:
            raise StageDependencyError("shot detection produced no shots")

        document = {
            "schema_version": 1,
            "analysis_revision_id": context.revision.id,
            "duration_ms": context.episode.duration_ms,
            "proxy_sha256": context.outputs[Stage.PREPROCESS]["artifacts"]["proxy"]["sha256"],
            "provenance": provenance.to_dict(),
            "shots": [shot.to_dict() for shot in shots],
        }
        self._schemas.validate("shots", document)
        shots_artifact = self._write_json(
            context,
            f"scenes/{context.revision.id}.shots.json",
            document,
            kind=ARTIFACT_SHOTS,
            role="shots",
            producer_version=provenance.producer_version,
            cache_key=input_hash,
        )
        artifacts = [shots_artifact, *keyframe_artifacts]
        output = {
            "artifacts": {a.role: a.summary() for a in artifacts},
            "artifact_count": len(artifacts),
            "counts": {"shots": len(shots)},
            "keyframe_count": len(keyframe_artifacts),
            "provenance": provenance.to_dict(),
        }
        return _StageOutcome(
            output=output,
            artifacts=artifacts,
            rows=lambda: self._shots.insert(context.revision.id, shots),
        )

    def _run_build_scenes(self, context: _Context, input_hash: str) -> _StageOutcome:
        revision_id = context.revision.id
        provenance = self._provenance(Stage.BUILD_SCENES, context)
        transcript = self._load_transcript(context)
        shots = self._shots.load(revision_id)
        if not shots:
            raise StageDependencyError("scene grouping requires persisted shots")
        candidates = group_shots_into_scenes(shots, transcript)

        scenes: list[Scene] = []
        participants: list[tuple[str, tuple[str, ...]]] = []
        confidences: dict[str, float] = {}
        previous_summary: str | None = None
        for candidate in candidates:
            package = ScenePackage(
                scene_ordinal=candidate.ordinal,
                range=candidate.range,
                shot_ids=candidate.shot_ids,
                transcript_slice=transcript.slice(candidate.range),
                keyframes=candidate.keyframes,
                provenance=provenance,
                previous_summary=previous_summary,
                excluded_reasons=candidate.excluded_reasons,
            )
            label = ensure_scene_label(self._scene.analyze(package), candidate.ordinal)
            scene_id = new_id()
            scenes.append(
                Scene(
                    id=scene_id,
                    analysis_revision_id=revision_id,
                    ordinal=candidate.ordinal,
                    range=candidate.range,
                    shot_ids=candidate.shot_ids,
                    location=label.location,
                    summary=label.summary,
                    confidence=label.confidence,
                    provenance=provenance,
                )
            )
            participants.append((scene_id, label.participants))
            confidences[scene_id] = label.confidence
            previous_summary = label.summary

        characters, scene_links = resolve_characters(revision_id, participants, new_id)
        by_id = {character.id: character for character in characters}
        labelled = tuple(
            Scene(
                id=scene.id,
                analysis_revision_id=scene.analysis_revision_id,
                ordinal=scene.ordinal,
                range=scene.range,
                shot_ids=scene.shot_ids,
                location=scene.location,
                summary=scene.summary,
                confidence=scene.confidence,
                provenance=scene.provenance,
                character_ids=tuple(
                    sorted(
                        character_id
                        for (link_scene, character_id) in scene_links
                        if link_scene == scene.id and character_id in by_id
                    )
                ),
            )
            for scene in scenes
        )
        document = {
            "schema_version": 1,
            "analysis_revision_id": revision_id,
            "provenance": provenance.to_dict(),
            "scenes": [scene.to_dict() for scene in labelled],
        }
        self._schemas.validate("scenes", document)
        artifact = self._write_json(
            context,
            f"scenes/{revision_id}.scenes.json",
            document,
            kind=ARTIFACT_SCENES,
            role="scenes",
            producer_version=provenance.producer_version,
            cache_key=input_hash,
        )

        def rows() -> None:
            self._characters.insert(characters)
            self._scenes.insert(labelled)
            for scene_id, character_id in scene_links:
                self._characters.link_scene(
                    scene_id, character_id, revision_id, confidences.get(scene_id, 0.0)
                )

        output = {
            "artifacts": {artifact.role: artifact.summary()},
            "artifact_count": 1,
            "counts": {"scenes": len(labelled), "characters": len(characters)},
            "provenance": provenance.to_dict(),
        }
        return _StageOutcome(output=output, artifacts=[artifact], rows=rows)

    def _run_extract_events(self, context: _Context, input_hash: str) -> _StageOutcome:
        revision_id = context.revision.id
        provenance = self._provenance(Stage.EXTRACT_EVENTS, context)
        transcript = self._load_transcript(context)
        scenes = self._scenes.load(revision_id)
        if not scenes:
            raise StageDependencyError("event extraction requires persisted scenes")
        shots = {shot.id: shot for shot in self._shots.load(revision_id)}
        characters = self._characters.load(revision_id)
        by_key = {canonical_key(c.canonical_name): c.id for c in characters}
        unknown_id = by_key[canonical_key(UNKNOWN_CHARACTER_NAME)]
        keyframe_artifacts = self._keyframe_artifact_ids(context)

        events: list[Event] = []
        ordinal = 0
        for scene in scenes:
            keyframes = tuple(
                frame for shot_id in scene.shot_ids for frame in shots[shot_id].keyframes
            )
            package = ScenePackage(
                scene_ordinal=scene.ordinal,
                range=scene.range,
                shot_ids=scene.shot_ids,
                transcript_slice=transcript.slice(scene.range),
                keyframes=keyframes,
                provenance=provenance,
            )
            drafts = ensure_event_drafts(self._story.extract_events(package), package)
            for draft in drafts:
                event_id = new_id()
                evidence = tuple(
                    EvidenceItem(
                        id=new_id(),
                        event_id=event_id,
                        analysis_revision_id=revision_id,
                        type=EvidenceType(item.type),
                        range=TimeRange(item.start_ms, item.end_ms),
                        confidence=item.confidence,
                        transcript_id=item.transcript_ref,
                        artifact_id=(
                            keyframe_artifacts.get(item.keyframe_timestamp_ms)
                            if item.type == EvidenceType.VISUAL.value
                            else None
                        ),
                    )
                    for item in draft.evidence
                )
                for item in evidence:
                    if item.type is EvidenceType.VISUAL and item.artifact_id is None:
                        raise StageDependencyError(
                            "visual evidence references a keyframe with no published artifact"
                        )
                events.append(
                    Event(
                        id=event_id,
                        scene_id=scene.id,
                        analysis_revision_id=revision_id,
                        ordinal=ordinal,
                        range=TimeRange(draft.start_ms, draft.end_ms),
                        action=draft.action,
                        cause=draft.cause,
                        consequence=draft.consequence,
                        importance=draft.importance,
                        confidence=draft.confidence,
                        evidence=evidence,
                        character_ids=tuple(
                            dict.fromkeys(
                                by_key.get(canonical_key(name), unknown_id)
                                for name in draft.participants
                            )
                        ),
                    )
                )
                ordinal += 1
        if not events:
            raise StageDependencyError("event extraction produced no events")

        document = {
            "schema_version": 1,
            "analysis_revision_id": revision_id,
            "provenance": provenance.to_dict(),
            "characters": [character.to_dict() for character in characters],
            "events": [event.to_dict() for event in events],
            "evidence": [item.to_dict() for event in events for item in event.evidence],
        }
        self._schemas.validate("events", document)
        artifact = self._write_json(
            context,
            f"story/{revision_id}.events.json",
            document,
            kind=ARTIFACT_EVENTS,
            role="events",
            producer_version=provenance.producer_version,
            cache_key=input_hash,
        )
        output = {
            "artifacts": {artifact.role: artifact.summary()},
            "artifact_count": 1,
            "counts": {
                "events": len(events),
                "evidence_items": sum(len(event.evidence) for event in events),
            },
            "provenance": provenance.to_dict(),
        }
        return _StageOutcome(
            output=output, artifacts=[artifact], rows=lambda: self._events.insert(events)
        )

    def _run_build_story_graph(self, context: _Context, input_hash: str) -> _StageOutcome:
        revision_id = context.revision.id
        provenance = self._provenance(Stage.BUILD_STORY_GRAPH, context)
        events = self._events.load(revision_id)
        if not events:
            raise StageDependencyError("story reasoning requires persisted events")
        known_ids = [event.id for event in events]
        reasoning = ensure_story_reasoning(
            self._story.reason_story([event.to_dict() for event in events], known_ids),
            known_ids,
        )

        plots = tuple(
            Plot(
                id=new_id(),
                analysis_revision_id=revision_id,
                ordinal=index,
                title=draft.title,
                summary=draft.summary,
                importance=draft.importance,
                members=tuple(
                    PlotMember(
                        event_id=event_id,
                        ordinal=member_index,
                        membership_score=draft.membership_scores[member_index],
                    )
                    for member_index, event_id in enumerate(draft.event_ids)
                ),
            )
            for index, draft in enumerate(reasoning.plots)
        )
        edges = tuple(
            StoryEdge(
                id=new_id(),
                analysis_revision_id=revision_id,
                from_event_id=draft.from_event_id,
                to_event_id=draft.to_event_id,
                relation=draft.relation,
                confidence=draft.confidence,
            )
            for draft in reasoning.edges
        )
        graph = StoryGraph(
            analysis_revision_id=revision_id,
            events=events,
            plots=plots,
            edges=edges,
            provenance=provenance,
        )
        document = graph.to_dict()
        self._schemas.validate("story-graph", document)
        artifact = self._write_json(
            context,
            f"story/{revision_id}.storygraph.json",
            document,
            kind=ARTIFACT_STORY_GRAPH,
            role="story_graph",
            producer_version=provenance.producer_version,
            cache_key=input_hash,
        )
        output = {
            "artifacts": {artifact.role: artifact.summary()},
            "artifact_count": 1,
            "counts": {"plots": len(plots), "story_edges": len(edges)},
            "provenance": provenance.to_dict(),
        }
        return _StageOutcome(
            output=output,
            artifacts=[artifact],
            rows=lambda: self._story_rows.insert(plots, edges),
        )

    def _run_plan_recap(self, context: _Context, input_hash: str) -> _StageOutcome:
        revision_id = context.revision.id
        provenance = self._provenance(Stage.PLAN_RECAP, context)
        graph = self.load_story_graph(revision_id, provenance)
        plan = build_plan(
            plan_id=new_id(),
            recap_revision_id=context.recap_revision.id,
            graph=graph,
            profile=context.profile,
            provenance=provenance,
        )
        document = plan.to_dict()
        self._schemas.validate("recap-plan", document)
        artifact = self._write_json(
            context,
            f"recap/{context.recap_revision.id}.plan.json",
            document,
            kind=ARTIFACT_RECAP_PLAN,
            role="plan",
            producer_version=provenance.producer_version,
            cache_key=input_hash,
        )
        output = {
            "artifacts": {artifact.role: artifact.summary()},
            "artifact_count": 1,
            "counts": {
                "recap_plans": 1,
                "recap_plan_events": len(plan.ordered_events),
            },
            "plan_id": plan.id,
            "estimated_duration_ms": plan.estimated_duration_ms,
            "provenance": provenance.to_dict(),
        }
        return _StageOutcome(
            output=output, artifacts=[artifact], rows=lambda: self._plans.insert(plan)
        )

    # -- read helpers --------------------------------------------------------

    def load_story_graph(self, revision_id: str, provenance: Provenance) -> StoryGraph:
        """Rebuild the persisted StoryGraph; endpoints are revalidated on construction."""
        events = self._events.load(revision_id)
        if not events:
            raise StageDependencyError("no persisted events for this analysis revision")
        return StoryGraph(
            analysis_revision_id=revision_id,
            events=events,
            plots=self._story_rows.load_plots(revision_id),
            edges=self._story_rows.load_edges(revision_id),
            provenance=provenance,
        )

    def _load_transcript(self, context: _Context) -> Transcript:
        """Rebuild the transcript from persisted rows plus its published artifact.

        Scalar metadata comes from the artifact bytes the checkpoint hash-verified,
        so no forgeable checkpoint field can change what later stages see.
        """
        document = self._read_json(context, Stage.TRANSCRIBE, "transcript")
        transcript = self._transcripts.load(
            context.revision.id,
            language=document["language"],
            duration_ms=document["duration_ms"],
            audio_sha256=document["audio_sha256"],
            provenance=self._provenance(Stage.TRANSCRIBE, context),
        )
        if len(transcript.segments) != len(document["segments"]):
            raise CheckpointIntegrityError(
                "transcript rows ({0}) disagree with the published artifact ({1})".format(
                    len(transcript.segments), len(document["segments"])
                )
            )
        return transcript

    def _keyframe_artifact_ids(self, context: _Context) -> dict[int, str]:
        """Timestamp to artifact id, read from the checkpoint's own artifact rows."""
        scope_type, scope_id = context.scope(Stage.DETECT_SHOTS)
        checkpoint = self._checkpoints.find(Stage.DETECT_SHOTS.value, scope_type, scope_id)
        if checkpoint is None:
            raise StageDependencyError("shot detection has no published checkpoint")
        mapping: dict[int, str] = {}
        for row in self._checkpoints.artifact_rows(checkpoint["id"]):
            if row["kind"] != ARTIFACT_KEYFRAME:
                continue
            timestamp = row["role"].split(":", 1)[1]
            mapping[int(timestamp)] = row["id"]
        return mapping

    def _artifact_path(self, context: _Context, stage: Stage, role: str) -> Path:
        summary = context.outputs[stage]["artifacts"][role]
        path = context.layout.resolve(Path(summary["path"]))
        if not path.is_file():
            raise StageDependencyError(f"{stage.value} artifact {role} is missing: {path}")
        return path

    def _read_json(self, context: _Context, stage: Stage, role: str) -> dict[str, Any]:
        summary = context.outputs[stage]["artifacts"][role]
        path = self._artifact_path(context, stage, role)
        payload = path.read_bytes()
        if hash_bytes(payload) != summary["sha256"]:
            raise CheckpointIntegrityError(f"{stage.value} artifact {role} drifted on disk")
        document = json.loads(payload)
        if not isinstance(document, dict):
            raise CheckpointIntegrityError(f"{stage.value} artifact {role} is not an object")
        return document

    # -- artifact helpers ----------------------------------------------------

    @staticmethod
    def _file_identity(path: Path) -> tuple[str, int]:
        if not path.is_file():
            raise CheckpointIntegrityError(f"expected artifact is missing: {path}")
        return hash_file(path), path.stat().st_size

    @staticmethod
    def _sub_key(input_hash: str, discriminator: str) -> str:
        return hash_bytes(f"{input_hash}\x00{discriminator}".encode("utf-8"))

    def _artifact(
        self, kind: str, role: str, path: Path, producer_version: str, cache_key: str
    ) -> _PublishedArtifact:
        digest, size = self._file_identity(path)
        return _PublishedArtifact(
            kind=kind,
            role=role,
            path=path,
            sha256=digest,
            size=size,
            producer_version=producer_version,
            cache_key=cache_key,
        )

    def _write_json(
        self,
        context: _Context,
        relative: str,
        document: dict[str, Any],
        *,
        kind: str,
        role: str,
        producer_version: str,
        cache_key: str,
    ) -> _PublishedArtifact:
        payload = _dumps(document)
        path = context.layout.write_atomic(relative, payload)
        return _PublishedArtifact(
            kind=kind,
            role=role,
            path=path,
            sha256=hash_bytes(payload),
            size=len(payload),
            producer_version=producer_version,
            cache_key=cache_key,
        )
