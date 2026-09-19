"""Story-reasoning port: event extraction plus plot/causal-edge discovery.

Two provider-neutral calls, both validated at this boundary:

- `extract_events(package)` returns atomic event drafts inside one scene, each
  carrying at least one evidence reference.
- `reason_story(events)` returns a variable number of plots and typed causal edges
  over exactly the event ids the caller supplied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

from ..domain.errors import ProviderOutputInvalidError, ValidationError
from ..domain.scene.scene import ScenePackage
from ..domain.story.graph import EdgeRelation
from ..domain.time_range import TimeRange, validate_confidence

_EVIDENCE_TYPES = ("TRANSCRIPT", "VISUAL", "TIME_RANGE")


@dataclass(frozen=True)
class EvidenceDraftDTO:
    type: str
    start_ms: int
    end_ms: int
    confidence: float
    transcript_ref: str | None = None
    keyframe_timestamp_ms: int | None = None


@dataclass(frozen=True)
class EventDraftDTO:
    start_ms: int
    end_ms: int
    action: str
    cause: str
    consequence: str
    importance: float
    confidence: float
    participants: tuple[str, ...] = field(default_factory=tuple)
    evidence: tuple[EvidenceDraftDTO, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PlotDraftDTO:
    title: str
    summary: str
    importance: float
    event_ids: tuple[str, ...]
    membership_scores: tuple[float, ...]


@dataclass(frozen=True)
class EdgeDraftDTO:
    from_event_id: str
    to_event_id: str
    relation: EdgeRelation
    confidence: float


@dataclass(frozen=True)
class StoryReasoningDTO:
    plots: tuple[PlotDraftDTO, ...]
    edges: tuple[EdgeDraftDTO, ...]


def _require_ms(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProviderOutputInvalidError(
            f"{field_name} must be an integer millisecond value, got {value!r}"
        )
    if value < 0:
        raise ProviderOutputInvalidError(f"{field_name} must not be negative, got {value!r}")
    return value


def _confidence(value: object, field_name: str) -> float:
    try:
        return validate_confidence(value, field_name)
    except ValidationError as exc:
        raise ProviderOutputInvalidError(str(exc)) from exc


def parse_event_drafts(payload: object, package: ScenePackage) -> tuple[EventDraftDTO, ...]:
    """Validate event drafts against the authoritative scene geometry."""
    if not isinstance(payload, list) or not payload:
        raise ProviderOutputInvalidError("event extraction must return a non-empty list")
    transcript_ids = {segment.id for segment in package.transcript_slice}
    keyframe_times = {frame.timestamp_ms for frame in package.keyframes}

    drafts: list[EventDraftDTO] = []
    for index, raw in enumerate(payload):
        if not isinstance(raw, dict):
            raise ProviderOutputInvalidError(f"event draft {index} is not an object")
        start_ms = _require_ms(raw.get("start_ms"), f"event {index} start_ms")
        end_ms = _require_ms(raw.get("end_ms"), f"event {index} end_ms")
        if end_ms <= start_ms:
            raise ProviderOutputInvalidError(f"event {index} end_ms must exceed start_ms")
        try:
            event_range = TimeRange(start_ms, end_ms)
            event_range.assert_within(package.range, f"event {index}")
        except ValidationError as exc:
            raise ProviderOutputInvalidError(str(exc)) from exc
        action = raw.get("action")
        if not isinstance(action, str) or not action.strip():
            raise ProviderOutputInvalidError(f"event {index} action must be non-empty text")
        for key in ("cause", "consequence"):
            if not isinstance(raw.get(key, ""), str):
                raise ProviderOutputInvalidError(f"event {index} {key} must be text")
        raw_evidence = raw.get("evidence") or []
        if not isinstance(raw_evidence, list) or not raw_evidence:
            raise ProviderOutputInvalidError(f"event {index} carries no source evidence")
        evidence: list[EvidenceDraftDTO] = []
        for item_index, item in enumerate(raw_evidence):
            label = f"event {index} evidence {item_index}"
            if not isinstance(item, dict):
                raise ProviderOutputInvalidError(f"{label} is not an object")
            kind = item.get("type")
            if kind not in _EVIDENCE_TYPES:
                raise ProviderOutputInvalidError(f"{label} has unknown type {kind!r}")
            item_start = _require_ms(item.get("start_ms"), f"{label} start_ms")
            item_end = _require_ms(item.get("end_ms"), f"{label} end_ms")
            if item_end <= item_start:
                raise ProviderOutputInvalidError(f"{label} end_ms must exceed start_ms")
            try:
                TimeRange(item_start, item_end).assert_within(event_range, label)
            except ValidationError as exc:
                raise ProviderOutputInvalidError(str(exc)) from exc
            transcript_ref = item.get("transcript_ref")
            keyframe_ms = item.get("keyframe_timestamp_ms")
            if kind == "TRANSCRIPT":
                if transcript_ref not in transcript_ids:
                    raise ProviderOutputInvalidError(
                        f"{label} references unknown transcript segment {transcript_ref!r}"
                    )
            elif kind == "VISUAL":
                if keyframe_ms not in keyframe_times:
                    raise ProviderOutputInvalidError(
                        f"{label} references unknown keyframe {keyframe_ms!r}"
                    )
            evidence.append(
                EvidenceDraftDTO(
                    type=kind,
                    start_ms=item_start,
                    end_ms=item_end,
                    confidence=_confidence(item.get("confidence", 1.0), f"{label} confidence"),
                    transcript_ref=transcript_ref if kind == "TRANSCRIPT" else None,
                    keyframe_timestamp_ms=keyframe_ms if kind == "VISUAL" else None,
                )
            )
        participants = raw.get("participants") or []
        if not isinstance(participants, list) or any(
            not isinstance(name, str) or not name.strip() for name in participants
        ):
            raise ProviderOutputInvalidError(f"event {index} participants must be a list of names")
        drafts.append(
            EventDraftDTO(
                start_ms=start_ms,
                end_ms=end_ms,
                action=action,
                cause=raw.get("cause", ""),
                consequence=raw.get("consequence", ""),
                importance=_confidence(raw.get("importance", 0.0), f"event {index} importance"),
                confidence=_confidence(raw.get("confidence", 0.0), f"event {index} confidence"),
                participants=tuple(participants),
                evidence=tuple(evidence),
            )
        )
    return tuple(drafts)


def parse_story_reasoning(payload: object, known_event_ids: Sequence[str]) -> StoryReasoningDTO:
    """Validate plots/edges against exactly the supplied event ids."""
    if not isinstance(payload, dict):
        raise ProviderOutputInvalidError(f"story reasoning must be an object, got {payload!r}")
    known = set(known_event_ids)
    raw_plots = payload.get("plots")
    if not isinstance(raw_plots, list) or not raw_plots:
        raise ProviderOutputInvalidError("story reasoning must return at least one plot")

    plots: list[PlotDraftDTO] = []
    for index, raw in enumerate(raw_plots):
        if not isinstance(raw, dict):
            raise ProviderOutputInvalidError(f"plot {index} is not an object")
        for key in ("title", "summary"):
            value = raw.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ProviderOutputInvalidError(f"plot {index} {key} must be non-empty text")
        event_ids = raw.get("event_ids")
        if not isinstance(event_ids, list) or not event_ids:
            raise ProviderOutputInvalidError(f"plot {index} must reference at least one event")
        unknown = [event_id for event_id in event_ids if event_id not in known]
        if unknown:
            raise ProviderOutputInvalidError(f"plot {index} references unknown events {unknown}")
        if len(set(event_ids)) != len(event_ids):
            raise ProviderOutputInvalidError(f"plot {index} references an event twice")
        scores = raw.get("membership_scores") or [1.0] * len(event_ids)
        if not isinstance(scores, list) or len(scores) != len(event_ids):
            raise ProviderOutputInvalidError(
                f"plot {index} membership_scores must match event_ids"
            )
        plots.append(
            PlotDraftDTO(
                title=raw["title"],
                summary=raw["summary"],
                importance=_confidence(raw.get("importance", 0.0), f"plot {index} importance"),
                event_ids=tuple(event_ids),
                membership_scores=tuple(
                    _confidence(score, f"plot {index} membership_score") for score in scores
                ),
            )
        )

    raw_edges = payload.get("edges") or []
    if not isinstance(raw_edges, list):
        raise ProviderOutputInvalidError("story reasoning edges must be a list")
    edges: list[EdgeDraftDTO] = []
    for index, raw in enumerate(raw_edges):
        if not isinstance(raw, dict):
            raise ProviderOutputInvalidError(f"edge {index} is not an object")
        source = raw.get("from_event_id")
        target = raw.get("to_event_id")
        for endpoint in (source, target):
            if endpoint not in known:
                raise ProviderOutputInvalidError(
                    f"edge {index} references unknown event {endpoint!r}"
                )
        if source == target:
            raise ProviderOutputInvalidError(f"edge {index} connects an event to itself")
        try:
            relation = EdgeRelation(raw.get("relation"))
        except ValueError as exc:
            raise ProviderOutputInvalidError(
                f"edge {index} has unsupported relation {raw.get('relation')!r}"
            ) from exc
        edges.append(
            EdgeDraftDTO(
                from_event_id=source,
                to_event_id=target,
                relation=relation,
                confidence=_confidence(raw.get("confidence", 1.0), f"edge {index} confidence"),
            )
        )
    return StoryReasoningDTO(plots=tuple(plots), edges=tuple(edges))


def ensure_event_drafts(value: object, package: ScenePackage) -> tuple[EventDraftDTO, ...]:
    """Re-validate event drafts at the boundary, DTO instances or raw payload."""
    if isinstance(value, tuple) and all(isinstance(item, EventDraftDTO) for item in value):
        payload = [
            {
                "start_ms": draft.start_ms,
                "end_ms": draft.end_ms,
                "action": draft.action,
                "cause": draft.cause,
                "consequence": draft.consequence,
                "importance": draft.importance,
                "confidence": draft.confidence,
                "participants": list(draft.participants),
                "evidence": [
                    {
                        "type": item.type,
                        "start_ms": item.start_ms,
                        "end_ms": item.end_ms,
                        "confidence": item.confidence,
                        "transcript_ref": item.transcript_ref,
                        "keyframe_timestamp_ms": item.keyframe_timestamp_ms,
                    }
                    for item in draft.evidence
                ],
            }
            for draft in value
        ]
        return parse_event_drafts(payload, package)
    return parse_event_drafts(value, package)


def ensure_story_reasoning(value: object, known_event_ids: Sequence[str]) -> StoryReasoningDTO:
    """Re-validate plots/edges at the boundary, DTO instance or raw payload."""
    if isinstance(value, StoryReasoningDTO):
        payload = {
            "plots": [
                {
                    "title": plot.title,
                    "summary": plot.summary,
                    "importance": plot.importance,
                    "event_ids": list(plot.event_ids),
                    "membership_scores": list(plot.membership_scores),
                }
                for plot in value.plots
            ],
            "edges": [
                {
                    "from_event_id": edge.from_event_id,
                    "to_event_id": edge.to_event_id,
                    "relation": edge.relation.value,
                    "confidence": edge.confidence,
                }
                for edge in value.edges
            ],
        }
        return parse_story_reasoning(payload, known_event_ids)
    return parse_story_reasoning(value, known_event_ids)


class StoryReasoningProvider(Protocol):
    name: str

    @property
    def version(self) -> str: ...

    @property
    def model(self) -> str: ...

    def extract_events(self, package: ScenePackage) -> tuple[EventDraftDTO, ...]: ...

    def reason_story(
        self, events: Sequence[dict[str, object]], known_event_ids: Sequence[str]
    ) -> StoryReasoningDTO: ...
