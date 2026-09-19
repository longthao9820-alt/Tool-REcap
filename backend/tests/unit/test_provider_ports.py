"""Provider boundary validation: malformed model output can never reach the domain."""

from __future__ import annotations

import pytest

from recap_core.domain.errors import ProviderOutputInvalidError
from recap_core.domain.provenance import Provenance
from recap_core.domain.scene.scene import ScenePackage
from recap_core.domain.scene.shot import Keyframe
from recap_core.domain.story.graph import EdgeRelation
from recap_core.domain.time_range import TimeRange
from recap_core.domain.transcript.transcript import TranscriptSegment
from recap_core.ports.scene_analysis import ensure_scene_label, parse_scene_label_dto
from recap_core.ports.story_reasoning import (
    ensure_event_drafts,
    ensure_story_reasoning,
    parse_event_drafts,
    parse_story_reasoning,
)
from recap_core.ports.stt import ensure_transcript_dto, parse_transcript_dto

SHA = "a" * 64


def transcript_payload(**overrides):
    payload = {
        "language": "vi",
        "model": "m",
        "provider_version": "v",
        "segments": [{"start_ms": 0, "end_ms": 500, "text": "xin chao"}],
    }
    payload.update(overrides)
    return payload


# -- STT ---------------------------------------------------------------------


def test_transcript_dto_accepts_a_valid_payload():
    dto = parse_transcript_dto(transcript_payload())
    assert dto.segments[0].confidence == 1.0
    assert dto.language == "vi"


@pytest.mark.parametrize(
    "payload",
    [
        "not-an-object",
        {},
        transcript_payload(language=" "),
        transcript_payload(segments=[]),
        transcript_payload(segments=[{"start_ms": 0.5, "end_ms": 5, "text": "a"}]),
        transcript_payload(segments=[{"start_ms": -1, "end_ms": 5, "text": "a"}]),
        transcript_payload(segments=[{"start_ms": 5, "end_ms": 5, "text": "a"}]),
        transcript_payload(segments=[{"start_ms": 0, "end_ms": 5, "text": "  "}]),
        transcript_payload(
            segments=[{"start_ms": 0, "end_ms": 5, "text": "a", "confidence": 1.5}]
        ),
        transcript_payload(
            segments=[
                {"start_ms": 0, "end_ms": 500, "text": "a"},
                {"start_ms": 400, "end_ms": 900, "text": "b"},
            ]
        ),
        transcript_payload(
            segments=[
                {
                    "start_ms": 0,
                    "end_ms": 500,
                    "text": "a",
                    "words": [{"text": "a", "start_ms": 0, "end_ms": 900}],
                }
            ]
        ),
        transcript_payload(model=""),
    ],
)
def test_transcript_dto_fails_typed_on_malformed_output(payload):
    with pytest.raises(ProviderOutputInvalidError):
        parse_transcript_dto(payload)


def test_ensure_transcript_dto_revalidates_a_dto_instance():
    dto = parse_transcript_dto(transcript_payload())
    assert ensure_transcript_dto(dto).segments == dto.segments
    with pytest.raises(ProviderOutputInvalidError):
        ensure_transcript_dto({"language": "vi"})


# -- scene analysis ----------------------------------------------------------


def scene_label(**overrides):
    payload = {
        "scene_ordinal": 0,
        "location": "phong khach",
        "summary": "hai nhan vat noi chuyen",
        "confidence": 0.7,
    }
    payload.update(overrides)
    return payload


def test_scene_label_accepts_labels_only():
    dto = parse_scene_label_dto(scene_label(participants=["An"]), 0)
    assert dto.location == "phong khach"
    assert dto.participants == ("An",)


@pytest.mark.parametrize(
    "payload",
    [
        scene_label(start_ms=0),
        scene_label(end_ms=100),
        scene_label(shot_ids=["s1"]),
        scene_label(id="forged"),
        scene_label(analysis_revision_id="other"),
    ],
)
def test_scene_label_cannot_override_authoritative_fields(payload):
    with pytest.raises(ProviderOutputInvalidError):
        parse_scene_label_dto(payload, 0)


@pytest.mark.parametrize(
    "payload,ordinal",
    [
        (scene_label(), 1),
        (scene_label(location=" "), 0),
        (scene_label(summary=""), 0),
        (scene_label(confidence=2), 0),
        (scene_label(participants=[1]), 0),
        ("nope", 0),
    ],
)
def test_scene_label_fails_typed_on_malformed_output(payload, ordinal):
    with pytest.raises(ProviderOutputInvalidError):
        parse_scene_label_dto(payload, ordinal)


def test_ensure_scene_label_revalidates_a_dto_instance():
    dto = parse_scene_label_dto(scene_label(), 0)
    assert ensure_scene_label(dto, 0) == dto
    with pytest.raises(ProviderOutputInvalidError):
        ensure_scene_label(dto, 1)


# -- story reasoning ---------------------------------------------------------


def package() -> ScenePackage:
    return ScenePackage(
        scene_ordinal=0,
        range=TimeRange(0, 1000),
        shot_ids=("shot-0",),
        transcript_slice=(
            TranscriptSegment(id="t0", ordinal=0, range=TimeRange(0, 400), text="chao"),
        ),
        keyframes=(Keyframe(timestamp_ms=200, path="f.jpg", sha256=SHA, size=10),),
        provenance=Provenance(source_sha256=SHA, producer="p", producer_version="1"),
    )


def event_draft(**overrides):
    payload = {
        "start_ms": 0,
        "end_ms": 400,
        "action": "nhan vat mo cua",
        "cause": "",
        "consequence": "",
        "importance": 0.5,
        "confidence": 0.5,
        "evidence": [
            {"type": "TRANSCRIPT", "start_ms": 0, "end_ms": 400, "transcript_ref": "t0"}
        ],
    }
    payload.update(overrides)
    return payload


def test_event_drafts_accept_in_range_evidence():
    drafts = parse_event_drafts([event_draft()], package())
    assert drafts[0].evidence[0].transcript_ref == "t0"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        "nope",
        [event_draft(end_ms=2000)],
        [event_draft(start_ms=400, end_ms=100)],
        [event_draft(action="  ")],
        [event_draft(evidence=[])],
        [event_draft(importance=3)],
        [
            event_draft(
                evidence=[
                    {"type": "TRANSCRIPT", "start_ms": 0, "end_ms": 400, "transcript_ref": "x"}
                ]
            )
        ],
        [
            event_draft(
                evidence=[
                    {
                        "type": "VISUAL",
                        "start_ms": 0,
                        "end_ms": 400,
                        "keyframe_timestamp_ms": 999,
                    }
                ]
            )
        ],
        [event_draft(evidence=[{"type": "GUESS", "start_ms": 0, "end_ms": 10}])],
        [event_draft(evidence=[{"type": "TIME_RANGE", "start_ms": 0, "end_ms": 900}])],
        [event_draft(participants=[""])],
    ],
)
def test_event_drafts_fail_typed_on_malformed_output(payload):
    with pytest.raises(ProviderOutputInvalidError):
        parse_event_drafts(payload, package())


def test_ensure_event_drafts_revalidates_dto_instances():
    drafts = parse_event_drafts([event_draft()], package())
    assert ensure_event_drafts(drafts, package()) == drafts


def reasoning(**overrides):
    payload = {
        "plots": [{"title": "t", "summary": "s", "importance": 0.5, "event_ids": ["e0", "e1"]}],
        "edges": [
            {"from_event_id": "e0", "to_event_id": "e1", "relation": "CAUSE", "confidence": 0.9}
        ],
    }
    payload.update(overrides)
    return payload


def test_story_reasoning_accepts_known_events_only():
    dto = parse_story_reasoning(reasoning(), ["e0", "e1"])
    assert dto.plots[0].event_ids == ("e0", "e1")
    assert dto.edges[0].relation is EdgeRelation.CAUSE


@pytest.mark.parametrize(
    "payload",
    [
        "nope",
        reasoning(plots=[]),
        reasoning(plots=[{"title": " ", "summary": "s", "event_ids": ["e0"]}]),
        reasoning(plots=[{"title": "t", "summary": "s", "event_ids": ["ghost"]}]),
        reasoning(plots=[{"title": "t", "summary": "s", "event_ids": ["e0", "e0"]}]),
        reasoning(
            plots=[
                {
                    "title": "t",
                    "summary": "s",
                    "event_ids": ["e0"],
                    "membership_scores": [1.0, 1.0],
                }
            ]
        ),
        reasoning(edges=[{"from_event_id": "e0", "to_event_id": "ghost", "relation": "CAUSE"}]),
        reasoning(edges=[{"from_event_id": "e0", "to_event_id": "e0", "relation": "CAUSE"}]),
        reasoning(edges=[{"from_event_id": "e0", "to_event_id": "e1", "relation": "MAYBE"}]),
        reasoning(edges="nope"),
    ],
)
def test_story_reasoning_fails_typed_on_malformed_output(payload):
    with pytest.raises(ProviderOutputInvalidError):
        parse_story_reasoning(payload, ["e0", "e1"])


def test_ensure_story_reasoning_revalidates_a_dto_instance():
    dto = parse_story_reasoning(reasoning(), ["e0", "e1"])
    assert ensure_story_reasoning(dto, ["e0", "e1"]) == dto
    with pytest.raises(ProviderOutputInvalidError):
        ensure_story_reasoning(dto, ["e0"])
