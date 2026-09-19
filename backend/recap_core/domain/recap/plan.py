"""StoryBudget and the basic RecapPlan derived from an immutable StoryGraph."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..errors import BudgetInfeasibleError, RevisionMismatchError, ValidationError
from ..provenance import Provenance
from ..time_range import validate_ms
from .profile import RecapProfile

SCHEMA_VERSION = 1


class BudgetClass(str, Enum):
    MUST_HAVE = "MUST_HAVE"
    SHOULD_HAVE = "SHOULD_HAVE"
    OPTIONAL = "OPTIONAL"


_CLASS_RANK = {BudgetClass.MUST_HAVE: 0, BudgetClass.SHOULD_HAVE: 1, BudgetClass.OPTIONAL: 2}


@dataclass(frozen=True)
class BudgetDecision:
    """One classified event plus the rationale that produced the class."""

    event_id: str
    budget_class: BudgetClass
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.budget_class, BudgetClass):
            raise ValidationError(f"unknown budget class: {self.budget_class!r}")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValidationError("budget decision reason must be non-empty text")

    @property
    def rank(self) -> int:
        return _CLASS_RANK[self.budget_class]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "budget_class": self.budget_class.value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class StoryBudget:
    """Complete classification of every event in one analysis revision."""

    analysis_revision_id: str
    decisions: tuple[BudgetDecision, ...]

    def __post_init__(self) -> None:
        if not self.decisions:
            raise ValidationError("story budget must classify at least one event")
        if len({decision.event_id for decision in self.decisions}) != len(self.decisions):
            raise ValidationError("story budget classifies an event twice")

    def by_class(self, budget_class: BudgetClass) -> tuple[BudgetDecision, ...]:
        return tuple(d for d in self.decisions if d.budget_class is budget_class)

    def decision_for(self, event_id: str) -> BudgetDecision:
        for decision in self.decisions:
            if decision.event_id == event_id:
                return decision
        raise ValidationError(f"event {event_id} is not classified in this budget")

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_revision_id": self.analysis_revision_id,
            "decisions": [decision.to_dict() for decision in self.decisions],
        }


@dataclass(frozen=True)
class PlannedEvent:
    event_id: str
    ordinal: int
    budget_class: BudgetClass
    decision_reason: str
    estimated_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool) or self.ordinal < 0:
            raise ValidationError("planned event ordinal must be an integer >= 0")
        if not isinstance(self.budget_class, BudgetClass):
            raise ValidationError(f"unknown budget class: {self.budget_class!r}")
        validate_ms(self.estimated_ms, "estimated_ms")
        if self.estimated_ms == 0:
            raise ValidationError("planned event estimated_ms must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "ordinal": self.ordinal,
            "budget_class": self.budget_class.value,
            "decision_reason": self.decision_reason,
            "estimated_ms": self.estimated_ms,
        }


@dataclass(frozen=True)
class RecapPlan:
    """Ordered selected events/plots for one recap revision.

    A MUST_HAVE event can only be absent together with an explicit omission
    reason, so causal bridges can never be dropped silently.
    """

    id: str
    recap_revision_id: str
    analysis_revision_id: str
    profile: RecapProfile
    budget: StoryBudget
    ordered_events: tuple[PlannedEvent, ...]
    selected_plot_ids: tuple[str, ...]
    provenance: Provenance
    must_have_omissions: tuple[dict[str, str], ...] = field(default_factory=tuple)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.ordered_events:
            raise ValidationError("recap plan must select at least one event")
        ordinals = [event.ordinal for event in self.ordered_events]
        if ordinals != list(range(len(ordinals))):
            raise ValidationError("recap plan events must be ordered from 0")
        selected = {event.event_id for event in self.ordered_events}
        if len(selected) != len(self.ordered_events):
            raise ValidationError("recap plan selects an event twice")
        for planned in self.ordered_events:
            decision = self.budget.decision_for(planned.event_id)
            if decision.budget_class is not planned.budget_class:
                raise ValidationError(
                    f"planned event {planned.event_id} contradicts its budget class"
                )
        omitted_reasons = {entry.get("event_id") for entry in self.must_have_omissions}
        for decision in self.budget.by_class(BudgetClass.MUST_HAVE):
            if decision.event_id in selected:
                continue
            if decision.event_id not in omitted_reasons:
                raise BudgetInfeasibleError(
                    f"MUST_HAVE event {decision.event_id} dropped without an explicit reason"
                )
        if self.budget.analysis_revision_id != self.analysis_revision_id:
            raise RevisionMismatchError("recap plan budget belongs to another analysis revision")
        if self.profile.max_duration_ms is not None:
            if self.estimated_duration_ms > self.profile.max_duration_ms:
                raise BudgetInfeasibleError(
                    "estimated {0}ms exceeds max_duration_ms {1}".format(
                        self.estimated_duration_ms, self.profile.max_duration_ms
                    )
                )

    @property
    def estimated_duration_ms(self) -> int:
        return sum(event.estimated_ms for event in self.ordered_events)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "recap_revision_id": self.recap_revision_id,
            "analysis_revision_id": self.analysis_revision_id,
            "profile": self.profile.to_dict(),
            "budget": self.budget.to_dict(),
            "ordered_events": [event.to_dict() for event in self.ordered_events],
            "selected_plots": list(self.selected_plot_ids),
            "dialogue_decisions": {
                "policy": self.profile.dialogue_policy.value,
                "language": self.profile.language,
            },
            "must_have_omissions": [dict(entry) for entry in self.must_have_omissions],
            "estimated_duration_ms": self.estimated_duration_ms,
            "provenance": self.provenance.to_dict(),
        }
