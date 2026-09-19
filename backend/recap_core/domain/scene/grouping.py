"""Deterministic shot-to-scene continuity grouping.

Grouping is authoritative and local: it owns scene geometry so a provider can only
label what local evidence already established. A new scene starts when a shot is
excluded, when the transcript goes silent across the shot boundary (a speech turn
break), or when the maximum scene duration guard would be exceeded.
"""

from __future__ import annotations

from ..errors import ValidationError
from ..time_range import TimeRange
from ..transcript.transcript import Transcript
from .scene import SceneCandidate
from .shot import Shot

GROUPER_VERSION = "continuity-grouper-1"
DEFAULT_MAX_SCENE_MS = 120_000
DEFAULT_SILENCE_GAP_MS = 1_500


def group_shots_into_scenes(
    shots: tuple[Shot, ...],
    transcript: Transcript | None = None,
    *,
    max_scene_ms: int = DEFAULT_MAX_SCENE_MS,
    silence_gap_ms: int = DEFAULT_SILENCE_GAP_MS,
) -> tuple[SceneCandidate, ...]:
    """Group ordered shots into scene candidates. Every shot lands in exactly one."""
    if not shots:
        raise ValidationError("cannot group scenes without shots")
    if max_scene_ms <= 0:
        raise ValidationError("max_scene_ms must be positive")
    ordered = sorted(shots, key=lambda shot: (shot.range.start_ms, shot.ordinal))
    if [shot.ordinal for shot in ordered] != [shot.ordinal for shot in shots]:
        raise ValidationError("shots must be supplied in timeline order")

    groups: list[list[Shot]] = [[ordered[0]]]
    for shot in ordered[1:]:
        current = groups[-1]
        span_ms = shot.range.end_ms - current[0].range.start_ms
        if (
            shot.is_excluded
            or current[-1].is_excluded
            or span_ms > max_scene_ms
            or _is_turn_break(current[-1], shot, transcript, silence_gap_ms)
        ):
            groups.append([shot])
        else:
            current.append(shot)
    return tuple(
        SceneCandidate(ordinal=index, shots=tuple(group))
        for index, group in enumerate(groups)
    )


def _is_turn_break(
    previous: Shot, current: Shot, transcript: Transcript | None, silence_gap_ms: int
) -> bool:
    """True when no speech bridges the boundary between two adjacent shots."""
    if transcript is None:
        return False
    boundary_start = max(0, previous.range.end_ms - silence_gap_ms)
    boundary_end = current.range.start_ms + silence_gap_ms
    if boundary_end <= boundary_start:
        return False
    return not transcript.slice(TimeRange(boundary_start, boundary_end))
