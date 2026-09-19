"""Domain invariants for time, provenance, revisions, transcript, shots and scenes."""

from __future__ import annotations

import math

import pytest

from recap_core.domain.analysis.revision import (
    AnalysisConfig,
    AnalysisRevision,
    RecapRevision,
    RevisionStatus,
)
from recap_core.domain.errors import ValidationError
from recap_core.domain.provenance import Provenance
from recap_core.domain.scene.grouping import group_shots_into_scenes
from recap_core.domain.scene.scene import Scene, SceneCandidate, ScenePackage
from recap_core.domain.scene.shot import Keyframe, Shot
from recap_core.domain.time_range import (
    TimeRange,
    validate_confidence,
    validate_ms,
    validate_non_empty_text,
)
from recap_core.domain.transcript.transcript import Transcript, TranscriptSegment, Word

SHA = "a" * 64
OTHER_SHA = "b" * 64


def provenance(**overrides) -> Provenance:
    values = {
        "source_sha256": SHA,
        "producer": "test",
        "producer_version": "1",
    }
    values.update(overrides)
    return Provenance(**values)


# -- time ranges -------------------------------------------------------------


@pytest.mark.parametrize("value", [1.0, "5", True, float("nan"), float("inf"), -1])
def test_validate_ms_rejects_non_integer_or_negative(value):
    with pytest.raises(ValidationError):
        validate_ms(value, "field")


def test_time_range_requires_positive_length():
    with pytest.raises(ValidationError):
        TimeRange(10, 10)
    with pytest.raises(ValidationError):
        TimeRange(10, 5)


def test_time_range_containment_and_overlap():
    outer = TimeRange(0, 1000)
    inner = TimeRange(100, 200)
    assert outer.contains(inner)
    assert not inner.contains(outer)
    assert inner.overlaps(TimeRange(150, 400))
    assert not inner.overlaps(TimeRange(200, 400))
    assert inner.duration_ms == 100


def test_time_range_rejects_ranges_past_episode_duration():
    with pytest.raises(ValidationError):
        TimeRange(0, 1001).assert_within_duration(1000, "shot")
    TimeRange(0, 1000).assert_within_duration(1000, "shot")


def test_time_range_round_trips_and_rejects_unknown_fields():
    assert TimeRange.from_dict({"start_ms": 1, "end_ms": 2}) == TimeRange(1, 2)
    with pytest.raises(ValidationError):
        TimeRange.from_dict({"start_ms": 1, "end_ms": 2, "extra": 3})
    with pytest.raises(ValidationError):
        TimeRange.from_dict([1, 2])


@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan"), float("inf"), True, "0.5"])
def test_confidence_must_be_finite_and_bounded(value):
    with pytest.raises(ValidationError):
        validate_confidence(value)


def test_non_empty_text_rejects_blank():
    assert validate_non_empty_text("x", "f") == "x"
    with pytest.raises(ValidationError):
        validate_non_empty_text("   ", "f")


# -- provenance --------------------------------------------------------------


def test_provenance_requires_valid_source_digest():
    with pytest.raises(ValidationError):
        provenance(source_sha256="not-a-digest")


def test_provenance_cache_key_changes_with_every_causal_part():
    base = provenance().cache_key("scope")
    assert base != provenance(producer_version="2").cache_key("scope")
    assert base != provenance(model="m").cache_key("scope")
    assert base != provenance(prompt_version="p").cache_key("scope")
    assert base != provenance(source_sha256=OTHER_SHA).cache_key("scope")
    assert base != provenance().cache_key("other-scope")
    assert base == provenance().cache_key("scope")


def test_provenance_round_trip_rejects_unknown_fields():
    payload = provenance(model="m").to_dict()
    assert Provenance.from_dict(payload).model == "m"
    with pytest.raises(ValidationError):
        Provenance.from_dict({**payload, "rogue": 1})


# -- revisions ---------------------------------------------------------------


def analysis_config(**overrides) -> AnalysisConfig:
    values = dict(
        proxy_version="p1",
        audio_version="a1",
        shot_version="s1",
        grouper_version="g1",
        stt_provider="stt",
        stt_version="v1",
        stt_model="m1",
        stt_language="vi",
        scene_provider="scene",
        scene_version="sv1",
        story_provider="story",
        story_version="tv1",
    )
    values.update(overrides)
    return AnalysisConfig(**values)


def test_analysis_config_hash_reacts_to_every_analysis_input():
    base = analysis_config().config_hash(SHA)
    assert base == analysis_config().config_hash(SHA)
    assert base != analysis_config().config_hash(OTHER_SHA)
    for field in (
        "proxy_version",
        "audio_version",
        "shot_version",
        "grouper_version",
        "stt_provider",
        "stt_version",
        "stt_model",
        "stt_language",
        "scene_provider",
        "scene_version",
        "story_provider",
        "story_version",
    ):
        assert analysis_config(**{field: "changed"}).config_hash(SHA) != base


def test_analysis_config_has_no_recap_side_input():
    """Analyze once, generate many: no profile field can enter the analysis hash."""
    fields = set(analysis_config().to_dict())
    assert not fields & {"style", "compression", "dialogue_policy", "language", "profile_hash"}


def test_analysis_config_rejects_blank_versions():
    with pytest.raises(ValidationError):
        analysis_config(stt_model="  ")


def test_revisions_validate_digests_and_status():
    revision = AnalysisRevision(
        id="r", episode_id="e", config_hash="c" * 64, source_sha256=SHA
    )
    assert revision.status is RevisionStatus.OPEN
    with pytest.raises(ValidationError):
        AnalysisRevision(id="r", episode_id="e", config_hash="short", source_sha256=SHA)
    with pytest.raises(ValidationError):
        RecapRevision(id="x", analysis_revision_id="r", profile_hash="nope")


# -- transcript --------------------------------------------------------------


def segment(index: int, start: int, end: int, **overrides) -> TranscriptSegment:
    values = dict(
        id=f"seg-{index}",
        ordinal=index,
        range=TimeRange(start, end),
        text=f"line {index}",
    )
    values.update(overrides)
    return TranscriptSegment(**values)


def transcript(*segments, duration_ms: int = 1000) -> Transcript:
    return Transcript(
        language="vi",
        duration_ms=duration_ms,
        audio_sha256=SHA,
        provenance=provenance(),
        segments=segments or (segment(0, 0, 500),),
    )


def test_transcript_requires_ordered_non_overlapping_segments():
    with pytest.raises(ValidationError):
        transcript(segment(0, 0, 500), segment(1, 400, 800))
    with pytest.raises(ValidationError):
        transcript(segment(1, 0, 500))
    with pytest.raises(ValidationError):
        transcript(segment(0, 0, 500), segment(1, 900, 1200))


def test_transcript_requires_at_least_one_segment():
    with pytest.raises(ValidationError):
        Transcript(
            language="vi",
            duration_ms=1000,
            audio_sha256=SHA,
            provenance=provenance(),
            segments=(),
        )


def test_transcript_slice_returns_overlapping_segments_only():
    document = transcript(segment(0, 0, 400), segment(1, 400, 800))
    assert [s.id for s in document.slice(TimeRange(350, 500))] == ["seg-0", "seg-1"]
    assert [s.id for s in document.slice(TimeRange(800, 900))] == []


def test_words_must_stay_inside_their_segment():
    with pytest.raises(ValidationError):
        segment(0, 0, 500, words=(Word(text="x", range=TimeRange(400, 600)),))


# -- shots and scenes --------------------------------------------------------


def keyframe(timestamp: int) -> Keyframe:
    return Keyframe(timestamp_ms=timestamp, path=f"f{timestamp}.jpg", sha256=SHA, size=10)


def shot(index: int, start: int, end: int, **overrides) -> Shot:
    values = dict(
        id=f"shot-{index}",
        ordinal=index,
        range=TimeRange(start, end),
        keyframes=(keyframe(start + (end - start) // 2),),
    )
    values.update(overrides)
    return Shot(**values)


def test_shot_requires_a_keyframe_inside_its_range():
    with pytest.raises(ValidationError):
        Shot(id="s", ordinal=0, range=TimeRange(0, 100), keyframes=())
    with pytest.raises(ValidationError):
        Shot(id="s", ordinal=0, range=TimeRange(0, 100), keyframes=(keyframe(100),))


def test_keyframe_validates_identity_and_size():
    with pytest.raises(ValidationError):
        Keyframe(timestamp_ms=0, path="f.jpg", sha256="bad", size=1)
    with pytest.raises(ValidationError):
        Keyframe(timestamp_ms=0, path="f.jpg", sha256=SHA, size=0)
    with pytest.raises(ValidationError):
        Keyframe(timestamp_ms=0, path="f.jpg", sha256=SHA, size=1, kind="GUESS")


def test_grouping_merges_shots_bridged_by_speech():
    shots = (shot(0, 0, 400), shot(1, 400, 800))
    speech = transcript(segment(0, 0, 800))
    scenes = group_shots_into_scenes(shots, speech)
    assert len(scenes) == 1
    assert scenes[0].shot_ids == ("shot-0", "shot-1")


def test_grouping_splits_on_a_silent_turn_break():
    shots = (shot(0, 0, 400), shot(1, 400, 800))
    speech = transcript(segment(0, 0, 100), segment(1, 700, 800))
    scenes = group_shots_into_scenes(shots, speech, silence_gap_ms=100)
    assert [scene.shot_ids for scene in scenes] == [("shot-0",), ("shot-1",)]


def test_grouping_splits_on_excluded_shots_and_duration_guard():
    excluded = shot(1, 400, 800, excluded_reason="credits")
    scenes = group_shots_into_scenes((shot(0, 0, 400), excluded, shot(2, 800, 1200)))
    assert [scene.shot_ids for scene in scenes] == [("shot-0",), ("shot-1",), ("shot-2",)]

    long_scenes = group_shots_into_scenes(
        (shot(0, 0, 400), shot(1, 400, 800)), max_scene_ms=500
    )
    assert len(long_scenes) == 2


def test_grouping_rejects_unordered_or_empty_input():
    with pytest.raises(ValidationError):
        group_shots_into_scenes(())
    with pytest.raises(ValidationError):
        group_shots_into_scenes((shot(1, 400, 800), shot(0, 0, 400)))
    with pytest.raises(ValidationError):
        group_shots_into_scenes((shot(0, 0, 400),), max_scene_ms=0)


def test_scene_candidate_exposes_local_geometry():
    candidate = SceneCandidate(ordinal=0, shots=(shot(0, 0, 400), shot(1, 400, 800)))
    assert candidate.range == TimeRange(0, 800)
    assert candidate.shot_ids == ("shot-0", "shot-1")
    assert len(candidate.keyframes) == 2
    with pytest.raises(ValidationError):
        SceneCandidate(ordinal=0, shots=())


def test_scene_package_rejects_transcript_outside_its_range():
    with pytest.raises(ValidationError):
        ScenePackage(
            scene_ordinal=0,
            range=TimeRange(0, 400),
            shot_ids=("shot-0",),
            transcript_slice=(segment(0, 500, 900),),
            keyframes=(keyframe(100),),
            provenance=provenance(),
        )


def test_scene_rejects_duplicate_or_missing_shot_references():
    common = dict(
        analysis_revision_id="rev",
        ordinal=0,
        range=TimeRange(0, 400),
        location="L",
        summary="S",
        confidence=0.5,
        provenance=provenance(),
    )
    with pytest.raises(ValidationError):
        Scene(id="s", shot_ids=(), **common)
    with pytest.raises(ValidationError):
        Scene(id="s", shot_ids=("a", "a"), **common)
    assert Scene(id="s", shot_ids=("a",), **common).to_dict()["shot_ids"] == ["a"]


def test_media_duration_conversion_is_finite_and_positive():
    from recap_core.domain.media.metadata import duration_ms_from_seconds

    assert duration_ms_from_seconds(1.5) == 1500
    for value in (0, -1, math.inf, math.nan, "x"):
        with pytest.raises(ValidationError):
            duration_ms_from_seconds(value)
