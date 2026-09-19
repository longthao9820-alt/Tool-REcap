"""Events, evidence, StoryGraph acceptance, character resolution and planning."""

from __future__ import annotations

import pytest

from recap_core.domain.errors import (
    BudgetInfeasibleError,
    RevisionMismatchError,
    ValidationError,
)
from recap_core.domain.provenance import Provenance
from recap_core.domain.recap.plan import (
    BudgetClass,
    BudgetDecision,
    PlannedEvent,
    RecapPlan,
    StoryBudget,
)
from recap_core.domain.recap.planner import build_plan, classify_budget, estimate_event_ms
from recap_core.domain.recap.profile import DialoguePolicy, RecapProfile, RecapStyle
from recap_core.domain.story.characters import (
    UNKNOWN_CHARACTER_NAME,
    canonical_key,
    is_unknown_label,
    resolve_characters,
)
from recap_core.domain.story.event import (
    Character,
    CharacterStatus,
    Event,
    EvidenceItem,
    EvidenceType,
)
from recap_core.domain.story.graph import (
    EdgeRelation,
    Plot,
    PlotMember,
    StoryEdge,
    StoryGraph,
)
from recap_core.domain.time_range import TimeRange

SHA = "a" * 64
REV = "rev-1"


def provenance() -> Provenance:
    return Provenance(source_sha256=SHA, producer="test", producer_version="1")


def evidence(event_id: str, start: int, end: int, **overrides) -> EvidenceItem:
    values = dict(
        id=f"ev-{event_id}-{start}",
        event_id=event_id,
        analysis_revision_id=REV,
        type=EvidenceType.TIME_RANGE,
        range=TimeRange(start, end),
    )
    values.update(overrides)
    return EvidenceItem(**values)


def event(
    index: int,
    start: int,
    end: int,
    *,
    importance: float = 0.5,
    revision: str = REV,
    characters: tuple[str, ...] = (),
) -> Event:
    event_id = f"e{index}"
    return Event(
        id=event_id,
        scene_id="scene-0",
        analysis_revision_id=revision,
        ordinal=index,
        range=TimeRange(start, end),
        action=f"hanh dong {index}",
        cause="",
        consequence=f"hau qua {index}",
        importance=importance,
        confidence=0.8,
        evidence=(
            EvidenceItem(
                id=f"ev-{event_id}",
                event_id=event_id,
                analysis_revision_id=revision,
                type=EvidenceType.TIME_RANGE,
                range=TimeRange(start, end),
            ),
        ),
        character_ids=characters,
    )


# -- events and evidence -----------------------------------------------------


def test_event_requires_non_empty_evidence():
    with pytest.raises(ValidationError):
        Event(
            id="e",
            scene_id="s",
            analysis_revision_id=REV,
            ordinal=0,
            range=TimeRange(0, 10),
            action="a",
            cause="",
            consequence="",
            importance=0.5,
            confidence=0.5,
            evidence=(),
        )


def test_evidence_must_belong_to_its_event_range_and_revision():
    base = event(0, 0, 100)
    with pytest.raises(ValidationError):
        Event(**{**vars(base), "evidence": (evidence("other", 0, 100),)})
    with pytest.raises(ValidationError):
        Event(**{**vars(base), "evidence": (evidence("e0", 0, 200),)})
    with pytest.raises(ValidationError):
        Event(
            **{
                **vars(base),
                "evidence": (
                    evidence("e0", 0, 100, analysis_revision_id="other-rev"),
                ),
            }
        )


def test_typed_evidence_requires_its_reference():
    with pytest.raises(ValidationError):
        evidence("e0", 0, 10, type=EvidenceType.TRANSCRIPT)
    with pytest.raises(ValidationError):
        evidence("e0", 0, 10, type=EvidenceType.VISUAL)
    assert evidence("e0", 0, 10, type=EvidenceType.TRANSCRIPT, transcript_id="t").transcript_id


# -- story graph -------------------------------------------------------------


def plot(event_ids: tuple[str, ...], revision: str = REV) -> Plot:
    return Plot(
        id="p1",
        analysis_revision_id=revision,
        ordinal=0,
        title="t",
        summary="s",
        importance=0.5,
        members=tuple(
            PlotMember(event_id=event_id, ordinal=index, membership_score=1.0)
            for index, event_id in enumerate(event_ids)
        ),
    )


def edge(source: str, target: str, relation=EdgeRelation.CAUSE, revision: str = REV) -> StoryEdge:
    return StoryEdge(
        id=f"edge-{source}-{target}",
        analysis_revision_id=revision,
        from_event_id=source,
        to_event_id=target,
        relation=relation,
    )


def graph(events, plots=None, edges=()) -> StoryGraph:
    return StoryGraph(
        analysis_revision_id=REV,
        events=tuple(events),
        plots=tuple(plots if plots is not None else (plot(tuple(e.id for e in events)),)),
        edges=tuple(edges),
        provenance=provenance(),
    )


def test_story_graph_rejects_unknown_edge_endpoints():
    with pytest.raises(RevisionMismatchError):
        graph([event(0, 0, 100), event(1, 100, 200)], edges=(edge("e0", "missing"),))


def test_story_graph_rejects_cross_revision_entities():
    with pytest.raises(RevisionMismatchError):
        graph([event(0, 0, 100, revision="other")])
    with pytest.raises(RevisionMismatchError):
        graph([event(0, 0, 100)], plots=(plot(("e0",), revision="other"),))
    with pytest.raises(RevisionMismatchError):
        graph(
            [event(0, 0, 100), event(1, 100, 200)],
            edges=(edge("e0", "e1", revision="other"),),
        )


def test_story_graph_rejects_duplicate_events_and_edges():
    with pytest.raises(ValidationError):
        graph([event(0, 0, 100), event(0, 0, 100)], plots=(plot(("e0",)),))
    with pytest.raises(ValidationError):
        graph(
            [event(0, 0, 100), event(1, 100, 200)],
            edges=(edge("e0", "e1"), edge("e0", "e1")),
        )


def test_story_edge_rejects_self_loops_and_unknown_relations():
    with pytest.raises(ValidationError):
        edge("e0", "e0")
    with pytest.raises(ValidationError):
        StoryEdge(
            id="x",
            analysis_revision_id=REV,
            from_event_id="e0",
            to_event_id="e1",
            relation="INVENTED",
        )


def test_bridge_event_ids_cover_causal_relations_only():
    story = graph(
        [event(0, 0, 100), event(1, 100, 200), event(2, 200, 300)],
        plots=(plot(("e0", "e1", "e2")),),
        edges=(edge("e0", "e1", EdgeRelation.SETUP), edge("e1", "e2", EdgeRelation.REACTION)),
    )
    assert story.bridge_event_ids() == frozenset({"e0", "e1"})


def test_plot_requires_ordered_unique_members():
    with pytest.raises(ValidationError):
        Plot(
            id="p",
            analysis_revision_id=REV,
            ordinal=0,
            title="t",
            summary="s",
            importance=0.5,
            members=(),
        )
    with pytest.raises(ValidationError):
        Plot(
            id="p",
            analysis_revision_id=REV,
            ordinal=0,
            title="t",
            summary="s",
            importance=0.5,
            members=(
                PlotMember(event_id="e0", ordinal=1, membership_score=1.0),
            ),
        )


# -- character resolution ----------------------------------------------------


@pytest.mark.parametrize("label", ["", "  ", "unknown", "UNKNOWN", "SPEAKER_02", "speaker 3"])
def test_unknown_labels_never_become_identities(label):
    assert is_unknown_label(label)


def test_resolve_characters_reuses_identities_and_keeps_unknown_placeholder():
    counter = iter(f"c{index}" for index in range(100))
    characters, links = resolve_characters(
        REV,
        [("scene-0", ("An", "UNKNOWN")), ("scene-1", ("an", "Binh"))],
        lambda: next(counter),
    )
    by_name = {character.canonical_name: character for character in characters}
    assert by_name[UNKNOWN_CHARACTER_NAME].status is CharacterStatus.UNKNOWN
    assert by_name["An"].status is CharacterStatus.RESOLVED
    assert len(characters) == 3
    assert (("scene-1", by_name["An"].id)) in links
    assert len(set(links)) == len(links)


def test_canonical_key_normalizes_case_and_spacing():
    assert canonical_key("  Nguyen   Van A ") == canonical_key("nguyen van a")


def test_character_requires_a_name_and_known_status():
    with pytest.raises(ValidationError):
        Character(id="c", analysis_revision_id=REV, canonical_name=" ")
    with pytest.raises(ValidationError):
        Character(id="c", analysis_revision_id=REV, canonical_name="A", status="GUESS")


# -- budgeting and planning --------------------------------------------------


def test_causal_bridges_and_reveals_are_must_have():
    story = graph(
        [event(0, 0, 100), event(1, 100, 200), event(2, 200, 300, importance=0.1)],
        plots=(plot(("e0", "e1", "e2")),),
        edges=(edge("e0", "e1", EdgeRelation.CAUSE), edge("e1", "e2", EdgeRelation.REVEAL)),
    )
    budget = classify_budget(story, RecapProfile())
    classes = {d.event_id: d.budget_class for d in budget.decisions}
    assert classes["e0"] is BudgetClass.MUST_HAVE
    assert classes["e1"] is BudgetClass.MUST_HAVE
    assert classes["e2"] is BudgetClass.MUST_HAVE
    assert all(decision.reason.strip() for decision in budget.decisions)


def test_low_importance_events_are_optional_without_causal_role():
    story = graph([event(0, 0, 100, importance=0.1), event(1, 100, 200, importance=0.5)])
    budget = classify_budget(story, RecapProfile())
    assert budget.decision_for("e0").budget_class is BudgetClass.OPTIONAL
    assert budget.decision_for("e1").budget_class is BudgetClass.SHOULD_HAVE


def test_character_style_prefers_the_focus_character():
    story = graph(
        [
            event(0, 0, 100, importance=0.1, characters=("c1",)),
            event(1, 100, 200, importance=0.1, characters=("c2",)),
        ]
    )
    profile = RecapProfile(style=RecapStyle.CHARACTER, focus_character_id="c1")
    budget = classify_budget(story, profile)
    assert budget.decision_for("e0").budget_class is BudgetClass.SHOULD_HAVE
    assert budget.decision_for("e1").budget_class is BudgetClass.OPTIONAL


def test_plan_is_chronological_and_keeps_must_have_events():
    story = graph(
        [event(0, 0, 100, importance=0.9), event(1, 100, 200), event(2, 200, 300)],
        plots=(plot(("e0", "e1", "e2")),),
        edges=(edge("e0", "e2", EdgeRelation.SETUP),),
    )
    plan = build_plan(
        plan_id="plan",
        recap_revision_id="recap",
        graph=story,
        profile=RecapProfile(),
        provenance=provenance(),
    )
    assert [planned.event_id for planned in plan.ordered_events] == ["e0", "e1", "e2"]
    assert [planned.ordinal for planned in plan.ordered_events] == [0, 1, 2]
    assert plan.estimated_duration_ms > 0
    assert plan.selected_plot_ids == ("p1",)


def test_plan_reports_conflict_instead_of_dropping_must_have():
    story = graph(
        [event(0, 0, 100, importance=0.9), event(1, 100, 200, importance=0.9)],
    )
    with pytest.raises(BudgetInfeasibleError):
        build_plan(
            plan_id="plan",
            recap_revision_id="recap",
            graph=story,
            profile=RecapProfile(max_duration_ms=100),
            provenance=provenance(),
        )


def test_plan_drops_optional_before_should_have_under_a_hard_bound():
    story = graph(
        [
            event(0, 0, 100, importance=0.9),
            event(1, 100, 200, importance=0.5),
            event(2, 200, 300, importance=0.1),
        ]
    )
    profile = RecapProfile(compression=1.0, max_duration_ms=4_500)
    plan = build_plan(
        plan_id="plan",
        recap_revision_id="recap",
        graph=story,
        profile=profile,
        provenance=provenance(),
    )
    selected = {planned.event_id for planned in plan.ordered_events}
    assert "e0" in selected
    assert "e2" not in selected
    assert plan.estimated_duration_ms <= 4_500


def test_estimate_reacts_to_dialogue_policy_and_speech_rate():
    sample = event(0, 0, 100)
    slow = RecapProfile(speech_rate_wps=0.5, dialogue_policy=DialoguePolicy.DIALOGUE_HEAVY)
    fast = RecapProfile(speech_rate_wps=8.0, dialogue_policy=DialoguePolicy.NARRATION_ONLY)
    assert estimate_event_ms(sample, slow) > estimate_event_ms(sample, fast)


def test_recap_plan_rejects_silent_must_have_omission():
    budget = StoryBudget(
        analysis_revision_id=REV,
        decisions=(
            BudgetDecision(event_id="e0", budget_class=BudgetClass.MUST_HAVE, reason="bridge"),
            BudgetDecision(event_id="e1", budget_class=BudgetClass.OPTIONAL, reason="low"),
        ),
    )
    planned = (
        PlannedEvent(
            event_id="e1",
            ordinal=0,
            budget_class=BudgetClass.OPTIONAL,
            decision_reason="low",
            estimated_ms=1000,
        ),
    )
    common = dict(
        id="plan",
        recap_revision_id="recap",
        analysis_revision_id=REV,
        profile=RecapProfile(),
        budget=budget,
        ordered_events=planned,
        selected_plot_ids=(),
        provenance=provenance(),
    )
    with pytest.raises(BudgetInfeasibleError):
        RecapPlan(**common)
    documented = RecapPlan(
        **common, must_have_omissions=({"event_id": "e0", "reason": "user removed"},)
    )
    assert documented.to_dict()["must_have_omissions"][0]["event_id"] == "e0"


def test_recap_plan_rejects_cross_revision_budget():
    budget = StoryBudget(
        analysis_revision_id="other",
        decisions=(
            BudgetDecision(event_id="e0", budget_class=BudgetClass.OPTIONAL, reason="low"),
        ),
    )
    with pytest.raises(RevisionMismatchError):
        RecapPlan(
            id="plan",
            recap_revision_id="recap",
            analysis_revision_id=REV,
            profile=RecapProfile(),
            budget=budget,
            ordered_events=(
                PlannedEvent(
                    event_id="e0",
                    ordinal=0,
                    budget_class=BudgetClass.OPTIONAL,
                    decision_reason="low",
                    estimated_ms=1000,
                ),
            ),
            selected_plot_ids=(),
            provenance=provenance(),
        )


def test_profile_hash_reacts_to_every_generation_input():
    base = RecapProfile().profile_hash()
    assert base == RecapProfile().profile_hash()
    assert base != RecapProfile(style=RecapStyle.CINEMATIC).profile_hash()
    assert base != RecapProfile(language="en").profile_hash()
    assert base != RecapProfile(compression=0.5).profile_hash()
    assert base != RecapProfile(dialogue_policy=DialoguePolicy.NARRATION_ONLY).profile_hash()
    assert base != RecapProfile(max_duration_ms=60_000).profile_hash()
    assert base != RecapProfile(speech_rate_wps=3.0).profile_hash()


def test_profile_validation_and_round_trip():
    with pytest.raises(ValidationError):
        RecapProfile(compression=0)
    with pytest.raises(ValidationError):
        RecapProfile(style=RecapStyle.CHARACTER)
    with pytest.raises(ValidationError):
        RecapProfile(speech_rate_wps=0)
    restored = RecapProfile.from_dict(RecapProfile(language="en").to_dict())
    assert restored.language == "en"
    with pytest.raises(ValidationError):
        RecapProfile.from_dict({"style": "NOPE"})
    with pytest.raises(ValidationError):
        RecapProfile.from_dict({"rogue": 1})
