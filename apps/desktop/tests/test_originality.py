from __future__ import annotations

from recap_tool.originality import audit_episode


def narration(segment_id: str, purpose: str, start: int, end: int) -> dict:
    return {
        "segment_id": segment_id,
        "order": 1,
        "segment_type": "narration",
        "purpose": purpose,
        "editorial_value": "Adds a supported explanation that is not already visible in the clip.",
        "narration_text": "The choice changes who controls the conflict and makes the next reversal possible.",
        "estimated_voice_duration_ms": 5000,
        "source_visual_duration_ms": end - start,
        "source_clips": [{"clip_id": segment_id + "-clip", "start_ms": start, "end_ms": end, "order": 1}],
    }


def test_original_commentary_plan_passes_internal_gate() -> None:
    episode = {
        "source_rights": {"status": "LICENSED", "notes": "Project record"},
        "outputs": [
            {
                "render_id": "story",
                "type": "MAIN_STORY",
                "segments": [
                    narration("s1", "CAUSAL_ANALYSIS", 0, 4000),
                    narration("s2", "CHARACTER_ANALYSIS", 5000, 9000),
                ],
            }
        ],
    }
    report = audit_episode(episode, content_type="US_TV_SHOW", recap_mode="MAIN_STORIES")
    assert report.passes
    assert report.metrics[0]["commentary_ratio"] == 1.0


def test_plot_description_and_dominant_source_dialogue_are_blocked() -> None:
    descriptive = narration("s1", "PLOT_DESCRIPTION", 0, 4000)
    dialogue = {
        "segment_id": "s2",
        "order": 2,
        "segment_type": "original_dialogue",
        "purpose": "ORIGINAL_DIALOGUE_EVIDENCE",
        "editorial_value": "Preserves the exact contradiction used by the following analysis.",
        "narration_text": "",
        "source_visual_duration_ms": 16000,
        "source_clips": [{"clip_id": "dialogue", "start_ms": 5000, "end_ms": 21000, "order": 1}],
    }
    episode = {
        "source_rights": {"status": "UNVERIFIED", "notes": "Review required"},
        "outputs": [{"render_id": "story", "type": "MAIN_STORY", "segments": [descriptive, dialogue]}],
    }
    report = audit_episode(episode, content_type="US_TV_SHOW", recap_mode="MAIN_STORIES")
    codes = {issue.code for issue in report.blocking_issues}
    assert "PLOT_DESCRIPTION_ONLY" in codes
    assert "LONG_ORIGINAL_DIALOGUE" in codes
    assert "COMMENTARY_NOT_CENTRAL" in codes


def test_rights_warning_is_separate_from_originality_gate() -> None:
    episode = {
        "source_rights": {"status": "UNVERIFIED", "notes": "Review required"},
        "outputs": [
            {
                "render_id": "story",
                "type": "MAIN_STORY",
                "segments": [
                    narration("s1", "CAUSAL_ANALYSIS", 0, 4000),
                    narration("s2", "CONSEQUENCE", 5000, 9000),
                ],
            }
        ],
    }
    report = audit_episode(episode, content_type="US_TV_SHOW", recap_mode="MAIN_STORIES")
    assert report.passes
    assert any(issue.code == "SOURCE_RIGHTS_UNVERIFIED" and issue.severity == "WARN" for issue in report.issues)
