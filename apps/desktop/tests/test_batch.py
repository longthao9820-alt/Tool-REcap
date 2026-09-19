from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from recap_tool.batch import _estimate_voice_duration_ms, import_recap_json


def segment(prefix: str) -> dict:
    return {
        "segment_id": f"{prefix}-segment",
        "order": 1,
        "segment_type": "narration",
        "purpose": "recap",
        "narration_text": "A short recap.",
        "estimated_voice_duration_ms": 2000,
        "source_clips": [
            {"clip_id": f"{prefix}-clip", "start_ms": 0, "end_ms": 2000, "order": 1}
        ],
        "subtitle": True,
    }


class BatchImportTests(unittest.TestCase):
    def test_imports_season_and_finds_videos(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Show.S01E01.mp4").write_bytes(b"video")
            (root / "Show.S01E02.mkv").write_bytes(b"video")
            payload = {
                "schema_version": "2.0",
                "project_type": "SEASON_BATCH",
                "project_name": "Show S01",
                "media_root_hint": ".",
                "episodes": [
                    {
                        "episode_id": "S01E01",
                        "source_file": "Show.S01E01.mp4",
                        "recap_language": "en-US",
                        "recap_mode": "FULL_EPISODE",
                        "outputs": [{"render_id": "full", "type": "FULL_RECAP", "title": "Full", "segments": [segment("a")] }],
                    },
                    {
                        "episode_id": "S01E02",
                        "source_file": "Show.S01E02.mkv",
                        "recap_language": "en-US",
                        "recap_mode": "MAIN_STORIES",
                        "outputs": [{"render_id": "story-1", "type": "MAIN_STORY", "title": "Story", "segments": [segment("b")] }],
                    },
                ],
            }
            json_path = root / "season.json"
            json_path.write_text(json.dumps(payload), encoding="utf-8")
            with patch.dict("os.environ", {"RECAP_STUDIO_DATA": str(root / "data")}):
                result = import_recap_json(json_path)
            self.assertEqual(result.found_count, 2)
            self.assertEqual(result.output_root, root / "recaps_da_render")
            self.assertEqual(result.episodes[1].output_count, 1)
            normalized = json.loads(result.episodes[0].manifest_path.read_text(encoding="utf-8"))
            first = normalized["outputs"][0]["segments"][0]
            self.assertEqual(first["original_audio"], "mute")
            self.assertFalse(first["preserve_original_audio"])
            self.assertEqual(first["source_visual_duration_ms"], 2000)
            self.assertEqual(first["recommended_visual_speed"], 1.0)
            self.assertTrue(normalized["render_policy"]["allow_frame_freeze"])

    def test_english_duration_fallback_is_conservative_and_nonzero(self) -> None:
        text = "The apparent victory creates a larger problem for the next episode."
        estimate = _estimate_voice_duration_ms(text, "en-US")
        self.assertGreater(estimate, 0)
        self.assertEqual(estimate % 100, 0)
        self.assertGreaterEqual(estimate, 4500)

    def test_imports_dual_mode_json_and_counts_each_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Show.S01E01.mp4").write_bytes(b"video")
            payload = {
                "schema_version": "2.2",
                "project_type": "SINGLE_EPISODE",
                "project_name": "Show S01E01",
                "media_root_hint": ".",
                "episodes": [
                    {
                        "episode_id": "S01E01",
                        "source_file": "Show.S01E01.mp4",
                        "recap_language": "en-US",
                        "recap_mode": "MAIN_STORIES",
                        "available_recap_modes": ["FULL_EPISODE", "MAIN_STORIES"],
                        "outputs": [
                            {"render_id": "full", "type": "FULL_RECAP", "title": "Full", "segments": [segment("full")]},
                            {"render_id": "story-1", "type": "MAIN_STORY", "title": "Story 1", "segments": [segment("story1")]},
                            {"render_id": "story-2", "type": "MAIN_STORY", "title": "Story 2", "segments": [segment("story2")]},
                        ],
                    }
                ],
            }
            json_path = root / "dual.json"
            json_path.write_text(json.dumps(payload), encoding="utf-8")
            with patch.dict("os.environ", {"RECAP_STUDIO_DATA": str(root / "data")}):
                result = import_recap_json(json_path)
            episode = result.episodes[0]
            self.assertEqual(episode.available_recap_modes, ("FULL_EPISODE", "MAIN_STORIES"))
            self.assertEqual(episode.output_count, 2)
            self.assertEqual(episode.output_counts_by_mode, {"FULL_EPISODE": 1, "MAIN_STORIES": 2})

    def test_import_rejects_future_tv_json_with_micro_clip_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Show.S01E01.mp4").write_bytes(b"video")
            bad_segment = segment("rapid")
            bad_segment["narration_text"] = "This plan relies on too many disconnected images to carry one short idea."
            bad_segment["source_clips"] = [
                {"clip_id": f"rapid-{index}", "start_ms": index * 1000, "end_ms": index * 1000 + 900, "order": index + 1}
                for index in range(6)
            ]
            payload = {
                "schema_version": "2.2",
                "project_type": "SINGLE_EPISODE",
                "project_name": "Show",
                "content_type": "US_TV_SHOW",
                "recap_language": "en-US",
                "episodes": [
                    {
                        "episode_id": "S01E01",
                        "source_file": "Show.S01E01.mp4",
                        "recap_language": "en-US",
                        "recap_mode": "MAIN_STORIES",
                        "outputs": [
                            {"render_id": "story", "type": "MAIN_STORY", "title": "A Smooth Story", "segments": [bad_segment]}
                        ],
                    }
                ],
            }
            path = root / "bad.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with patch.dict("os.environ", {"RECAP_STUDIO_DATA": str(root / "data")}), self.assertRaisesRegex(
                Exception, "Smooth-edit gate"
            ):
                import_recap_json(path)


if __name__ == "__main__":
    unittest.main()
