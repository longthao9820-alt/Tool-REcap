"""Deterministic story budgeting and basic recap planning.

The planner is pure domain logic over an immutable StoryGraph: it never touches
storage, media or providers, so changing a profile can only produce a new plan and
can never rewrite analysis output.
"""

from __future__ import annotations

import math

from ..errors import BudgetInfeasibleError
from ..provenance import Provenance
from ..story.event import Event
from ..story.graph import EdgeRelation, StoryGraph
from .plan import BudgetClass, BudgetDecision, PlannedEvent, RecapPlan, StoryBudget
from .profile import RecapProfile, RecapStyle

PLANNER_VERSION = "recap-planner-1"

MUST_HAVE_IMPORTANCE = 0.7
SHOULD_HAVE_IMPORTANCE = 0.4
MIN_NARRATION_MS = 1_200
DIALOGUE_HOLD_MS = {
    "NARRATION_ONLY": 0,
    "KEY_DIALOGUE": 600,
    "DIALOGUE_HEAVY": 1_200,
}
TRANSITION_MS = 250


def classify_budget(graph: StoryGraph, profile: RecapProfile) -> StoryBudget:
    """Assign a budget class and rationale to every event in the graph."""
    bridges = graph.bridge_event_ids()
    reveals = {
        edge.to_event_id for edge in graph.edges if edge.relation is EdgeRelation.REVEAL
    }
    decisions: list[BudgetDecision] = []
    for event in graph.events:
        budget_class, reason = _classify(event, profile, bridges, reveals)
        decisions.append(
            BudgetDecision(event_id=event.id, budget_class=budget_class, reason=reason)
        )
    return StoryBudget(analysis_revision_id=graph.analysis_revision_id, decisions=tuple(decisions))


def _classify(
    event: Event,
    profile: RecapProfile,
    bridges: frozenset[str],
    reveals: frozenset[str] | set[str],
) -> tuple[BudgetClass, str]:
    focused = (
        profile.focus_character_id is not None
        and profile.focus_character_id in event.character_ids
    )
    if event.id in bridges:
        return BudgetClass.MUST_HAVE, "causal bridge endpoint (cause/setup/payoff/consequence)"
    if event.id in reveals:
        return BudgetClass.MUST_HAVE, "major reveal target"
    if event.importance >= MUST_HAVE_IMPORTANCE:
        return BudgetClass.MUST_HAVE, f"narrative importance {event.importance:.2f}"
    if profile.style is RecapStyle.CHARACTER and focused:
        return BudgetClass.SHOULD_HAVE, "direct participation of focus character"
    if profile.style is RecapStyle.CHARACTER:
        return BudgetClass.OPTIONAL, "focus character does not participate"
    if event.importance >= SHOULD_HAVE_IMPORTANCE:
        return BudgetClass.SHOULD_HAVE, f"supporting context {event.importance:.2f}"
    return BudgetClass.OPTIONAL, f"low narrative importance {event.importance:.2f}"


def estimate_event_ms(event: Event, profile: RecapProfile) -> int:
    """Estimate spoken duration from narration length, dialogue hold and transition."""
    words = len(event.action.split()) + len(event.consequence.split())
    speech_ms = int(math.ceil(words / float(profile.speech_rate_wps) * 1000.0))
    hold_ms = DIALOGUE_HOLD_MS[profile.dialogue_policy.value]
    return max(MIN_NARRATION_MS, speech_ms) + hold_ms + TRANSITION_MS


def _style_keeps_should_have(profile: RecapProfile) -> bool:
    return profile.style in (RecapStyle.FULL_PLOT, RecapStyle.CHARACTER)


def build_plan(
    *,
    plan_id: str,
    recap_revision_id: str,
    graph: StoryGraph,
    profile: RecapProfile,
    provenance: Provenance,
) -> RecapPlan:
    """Select and order events for one recap revision.

    `max_duration_ms` is an upper bound, never a fill target. OPTIONAL is removed
    first, then SHOULD_HAVE; if MUST_HAVE coverage alone cannot fit, the conflict
    is reported instead of silently damaging the causal chain.
    """
    budget = classify_budget(graph, profile)
    estimates = {event.id: estimate_event_ms(event, profile) for event in graph.events}
    chronological = sorted(graph.events, key=lambda event: (event.range.start_ms, event.ordinal))

    must_have = [e for e in chronological if _class_of(budget, e.id) is BudgetClass.MUST_HAVE]
    should_have = [e for e in chronological if _class_of(budget, e.id) is BudgetClass.SHOULD_HAVE]
    optional = [e for e in chronological if _class_of(budget, e.id) is BudgetClass.OPTIONAL]

    selected: list[Event] = list(must_have)
    if _style_keeps_should_have(profile):
        selected.extend(should_have)
        keep_optional = max(0, int(len(optional) * float(profile.compression)))
        selected.extend(
            sorted(optional, key=lambda event: -event.importance)[:keep_optional]
        )
    else:
        keep_should = max(0, int(math.ceil(len(should_have) * float(profile.compression))))
        selected.extend(sorted(should_have, key=lambda event: -event.importance)[:keep_should])

    limit = profile.max_duration_ms
    if limit is not None:
        selected = _fit_within_limit(selected, budget, estimates, limit)

    selected_ids = {event.id for event in selected}
    ordered = sorted(selected, key=lambda event: (event.range.start_ms, event.ordinal))
    planned = tuple(
        PlannedEvent(
            event_id=event.id,
            ordinal=index,
            budget_class=_class_of(budget, event.id),
            decision_reason=budget.decision_for(event.id).reason,
            estimated_ms=estimates[event.id],
        )
        for index, event in enumerate(ordered)
    )
    selected_plots = tuple(
        plot.id
        for plot in graph.plots
        if any(member.event_id in selected_ids for member in plot.members)
    )
    return RecapPlan(
        id=plan_id,
        recap_revision_id=recap_revision_id,
        analysis_revision_id=graph.analysis_revision_id,
        profile=profile,
        budget=budget,
        ordered_events=planned,
        selected_plot_ids=selected_plots,
        provenance=provenance,
    )


def _class_of(budget: StoryBudget, event_id: str) -> BudgetClass:
    return budget.decision_for(event_id).budget_class


def _fit_within_limit(
    selected: list[Event],
    budget: StoryBudget,
    estimates: dict[str, int],
    limit_ms: int,
) -> list[Event]:
    """Drop OPTIONAL then SHOULD_HAVE to respect the hard upper bound."""
    remaining = list(selected)

    def total() -> int:
        return sum(estimates[event.id] for event in remaining)

    for droppable in (BudgetClass.OPTIONAL, BudgetClass.SHOULD_HAVE):
        candidates = sorted(
            (e for e in remaining if _class_of(budget, e.id) is droppable),
            key=lambda event: event.importance,
        )
        for candidate in candidates:
            if total() <= limit_ms:
                return remaining
            remaining.remove(candidate)
    if total() > limit_ms:
        raise BudgetInfeasibleError(
            "MUST_HAVE coverage needs {0}ms which exceeds max_duration_ms {1}".format(
                total(), limit_ms
            )
        )
    return remaining
