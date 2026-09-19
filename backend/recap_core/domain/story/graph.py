"""Plots and the immutable StoryGraph reused by every recap style."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..errors import RevisionMismatchError, ValidationError
from ..provenance import Provenance
from ..time_range import validate_confidence, validate_non_empty_text
from .event import Event

SCHEMA_VERSION = 1


class EdgeRelation(str, Enum):
    CAUSE = "CAUSE"
    EFFECT = "EFFECT"
    SETUP = "SETUP"
    PAYOFF = "PAYOFF"
    REVEAL = "REVEAL"
    REACTION = "REACTION"
    CONSEQUENCE = "CONSEQUENCE"


#: Edges whose endpoints must survive story budgeting as causal bridges.
BRIDGE_RELATIONS = frozenset(
    {EdgeRelation.CAUSE, EdgeRelation.SETUP, EdgeRelation.PAYOFF, EdgeRelation.CONSEQUENCE}
)


@dataclass(frozen=True)
class StoryEdge:
    """A typed relation between two events of the same analysis revision."""

    id: str
    analysis_revision_id: str
    from_event_id: str
    to_event_id: str
    relation: EdgeRelation
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.relation, EdgeRelation):
            raise ValidationError(f"unsupported story edge relation: {self.relation!r}")
        if self.from_event_id == self.to_event_id:
            raise ValidationError("story edge must connect two distinct events")
        object.__setattr__(self, "confidence", validate_confidence(self.confidence))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "analysis_revision_id": self.analysis_revision_id,
            "from_event_id": self.from_event_id,
            "to_event_id": self.to_event_id,
            "relation": self.relation.value,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class PlotMember:
    event_id: str
    ordinal: int
    membership_score: float

    def __post_init__(self) -> None:
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool) or self.ordinal < 0:
            raise ValidationError("plot member ordinal must be an integer >= 0")
        object.__setattr__(
            self, "membership_score", validate_confidence(self.membership_score, "membership_score")
        )


@dataclass(frozen=True)
class Plot:
    """Ordered event membership. The plot count is discovered, never fixed."""

    id: str
    analysis_revision_id: str
    ordinal: int
    title: str
    summary: str
    importance: float
    members: tuple[PlotMember, ...] = field(default_factory=tuple)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_non_empty_text(self.title, "plot title")
        validate_non_empty_text(self.summary, "plot summary")
        object.__setattr__(self, "importance", validate_confidence(self.importance, "importance"))
        if not self.members:
            raise ValidationError(f"plot {self.id} has no member events")
        ordinals = [member.ordinal for member in self.members]
        if ordinals != list(range(len(ordinals))):
            raise ValidationError(f"plot {self.id} members must be ordered from 0")
        if len({member.event_id for member in self.members}) != len(self.members):
            raise ValidationError(f"plot {self.id} references an event twice")

    @property
    def event_ids(self) -> tuple[str, ...]:
        return tuple(member.event_id for member in self.members)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "analysis_revision_id": self.analysis_revision_id,
            "ordinal": self.ordinal,
            "title": self.title,
            "summary": self.summary,
            "importance": self.importance,
            "event_ids": list(self.event_ids),
        }


@dataclass(frozen=True)
class StoryGraph:
    """Typed causal/semantic graph over the known events of one analysis revision.

    Construction is the acceptance boundary: an endpoint that does not exist in
    this revision, or an entity carrying a different revision id, fails closed.
    """

    analysis_revision_id: str
    events: tuple[Event, ...]
    plots: tuple[Plot, ...]
    edges: tuple[StoryEdge, ...]
    provenance: Provenance
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.events:
            raise ValidationError("story graph must contain at least one event")
        known: dict[str, Event] = {}
        for event in self.events:
            if event.analysis_revision_id != self.analysis_revision_id:
                raise RevisionMismatchError(
                    f"event {event.id} belongs to analysis revision "
                    f"{event.analysis_revision_id}, not {self.analysis_revision_id}"
                )
            if event.id in known:
                raise ValidationError(f"duplicate event {event.id} in story graph")
            known[event.id] = event

        for plot in self.plots:
            if plot.analysis_revision_id != self.analysis_revision_id:
                raise RevisionMismatchError(
                    f"plot {plot.id} belongs to another analysis revision"
                )
            for member in plot.members:
                if member.event_id not in known:
                    raise RevisionMismatchError(
                        f"plot {plot.id} references unknown event {member.event_id}"
                    )

        seen_edges: set[tuple[str, str, EdgeRelation]] = set()
        for edge in self.edges:
            if edge.analysis_revision_id != self.analysis_revision_id:
                raise RevisionMismatchError(
                    f"story edge {edge.id} belongs to another analysis revision"
                )
            for endpoint in (edge.from_event_id, edge.to_event_id):
                if endpoint not in known:
                    raise RevisionMismatchError(
                        f"story edge {edge.id} references unknown event {endpoint}"
                    )
            key = (edge.from_event_id, edge.to_event_id, edge.relation)
            if key in seen_edges:
                raise ValidationError(f"duplicate story edge {key}")
            seen_edges.add(key)

    @property
    def event_ids(self) -> tuple[str, ...]:
        return tuple(event.id for event in self.events)

    def event(self, event_id: str) -> Event:
        for event in self.events:
            if event.id == event_id:
                return event
        raise RevisionMismatchError(f"unknown event {event_id}")

    def bridge_event_ids(self) -> frozenset[str]:
        """Endpoints of causal/setup/payoff edges: never silently droppable."""
        bridges: set[str] = set()
        for edge in self.edges:
            if edge.relation in BRIDGE_RELATIONS:
                bridges.add(edge.from_event_id)
                bridges.add(edge.to_event_id)
        return frozenset(bridges)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "analysis_revision_id": self.analysis_revision_id,
            "event_ids": list(self.event_ids),
            "events": [event.to_dict() for event in self.events],
            "plots": [plot.to_dict() for plot in self.plots],
            "edges": [edge.to_dict() for edge in self.edges],
            "provenance": self.provenance.to_dict(),
        }
