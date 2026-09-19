from __future__ import annotations

from pathlib import Path

from recap_tool.renderer import (
    bounded_output_path,
    filesystem_safe_component,
    publication_title,
    RecapRenderer,
    split_subtitle_phrases,
    timed_subtitle_rows,
)


def test_intermediate_component_is_short_stable_and_unique() -> None:
    first = filesystem_safe_component("Woman-Begs-Police-for-Help-From-Toxic-Boyfriend-Bodycam-segment-002")
    second = filesystem_safe_component("Woman-Begs-Police-for-Help-From-Toxic-Boyfriend-Bodycam-segment-003")
    assert len(first) <= 36
    assert first == filesystem_safe_component("Woman-Begs-Police-for-Help-From-Toxic-Boyfriend-Bodycam-segment-002")
    assert first != second


def test_output_path_is_bounded_for_long_bodycam_names(tmp_path: Path) -> None:
    output_directory = tmp_path / ("long-bodycam-directory-" * 3)
    stem = "Woman-Begs-Police-for-Help-From-Toxic-Boyfriend-Bodycam-" * 5
    output = bounded_output_path(output_directory, stem, ".mp4")
    assert len(str(output)) <= 235
    assert output.suffix == ".mp4"


def test_narration_is_split_into_short_capcut_phrases() -> None:
    text = (
        "Tom offers proof with a price: once John sees it, responsibility becomes his. "
        "His chess metaphor makes the bargain clear."
    )
    phrases = split_subtitle_phrases(text)
    assert len(phrases) > 3
    assert " ".join(phrases) == text
    assert all(3 <= len(phrase.split()) <= 7 for phrase in phrases)


def test_timed_phrases_cover_segment_without_overlap() -> None:
    rows = timed_subtitle_rows(
        "After the attack on his family, John meets Tom at a remote location for answers.",
        13.43,
        17.809,
    )
    assert rows[0][0] == 13.43
    assert rows[-1][1] == 17.809
    assert all(first[1] == second[0] for first, second in zip(rows, rows[1:]))
    assert all(len(text.split()) <= 7 for _start, _end, text in rows)


def test_publication_title_is_exact_filename_base(tmp_path: Path) -> None:
    title = "John Dutton Turns the Ambush Into a Trap"
    assert publication_title(title, tmp_path) == title


def test_original_dialogue_subtitles_are_mapped_to_final_timeline() -> None:
    renderer = RecapRenderer()
    rows = renderer._original_subtitle_rows(
        {"segment_id": "hook", "segment_type": "original_dialogue"},
        [{"start_ms": 10_000, "end_ms": 15_000}],
        [{"start": 11.0, "end": 13.0, "text": "You already know the answer."}],
        20.0,
        5.0,
    )
    assert rows[0][0] == 21.0
    assert rows[-1][1] == 23.0
    assert " ".join(text for _start, _end, text in rows) == "You already know the answer."
