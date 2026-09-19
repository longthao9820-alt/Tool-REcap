from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from recap_tool.models import ProjectValidationError, load_project
from recap_tool.renderer import outputs_for_recap_mode


def valid_payload() -> dict:
    return {
        "schema_version": "1.0",
        "project_id": "du-an-test",
        "source_video": "video.mp4",
        "classification": {
            "content_type": "US_TV_SHOW",
            "source_language": "en-US",
            "recap_language": "en-US",
            "confidence": 0.95,
            "reason": "test",
        },
        "render_policy": {
            "aspect_ratio_policy": "preserve_source",
            "voice_speed": 1.0,
            "video_speed_absolute_min": 0.85,
            "video_speed_absolute_max": 1.15,
        },
        "outputs": [
            {
                "render_id": "highlight-01",
                "type": "HIGHLIGHT",
                "title": "Test",
                "segments": [
                    {
                        "segment_id": "segment-01",
                        "order": 1,
                        "segment_type": "narration",
                        "purpose": "hook",
                        "narration_text": "A short piece of commentary.",
                        "source_clips": [
                            {
                                "clip_id": "clip-01",
                                "source_video": "video.mp4",
                                "start_ms": 0,
                                "end_ms": 2000,
                                "order": 1,
                            }
                        ],
                    }
                ],
            }
        ],
    }


class ProjectModelTests(unittest.TestCase):
    def _write(self, payload: dict) -> Path:
        directory = Path(tempfile.mkdtemp())
        path = directory / "project.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_valid_project_loads(self) -> None:
        project = load_project(self._write(valid_payload()))
        self.assertEqual(project.project_id, "du-an-test")
        self.assertEqual(project.output_count, 1)
        self.assertEqual(project.segment_count, 1)

    def test_bodycam_content_type_is_supported(self) -> None:
        payload = valid_payload()
        payload["classification"]["content_type"] = "BODYCAM"
        project = load_project(self._write(payload))
        self.assertEqual(project.content_type, "BODYCAM")

    def test_voice_speed_must_be_one(self) -> None:
        payload = valid_payload()
        payload["render_policy"]["voice_speed"] = 1.1
        with self.assertRaises(ProjectValidationError):
            load_project(self._write(payload))

    def test_duplicate_clip_id_is_rejected(self) -> None:
        payload = valid_payload()
        duplicate = dict(payload["outputs"][0]["segments"][0]["source_clips"][0])
        duplicate["order"] = 2
        payload["outputs"][0]["segments"][0]["source_clips"].append(duplicate)
        with self.assertRaises(ProjectValidationError):
            load_project(self._write(payload))

    def test_renderer_selects_only_requested_recap_mode(self) -> None:
        payload = valid_payload()
        payload["recap_mode"] = "MAIN_STORIES"
        payload["outputs"][0]["type"] = "MAIN_STORY"
        payload["outputs"].append({
            "render_id": "full-recap",
            "type": "FULL_RECAP",
            "title": "Full",
            "segments": [{
                "segment_id": "full-segment",
                "order": 1,
                "segment_type": "narration",
                "narration_text": "Full episode recap.",
                "source_clips": [{
                    "clip_id": "full-clip", "source_video": "video.mp4",
                    "start_ms": 0, "end_ms": 2000, "order": 1,
                }],
            }],
        })
        project = load_project(self._write(payload))
        self.assertEqual([item["type"] for item in outputs_for_recap_mode(project, "FULL_EPISODE")], ["FULL_RECAP"])
        self.assertEqual([item["type"] for item in outputs_for_recap_mode(project, "MAIN_STORIES")], ["MAIN_STORY"])


if __name__ == "__main__":
    unittest.main()
