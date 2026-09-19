from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from recap_tool.batch import ImportedEpisode
from recap_tool.projects import ProjectRecord, ProjectStore


class ProjectTests(unittest.TestCase):
    def test_render_project_persists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "S01E01.mp4"
            manifest = root / "recap.json"
            video.write_bytes(b"video")
            manifest.write_text("{}", encoding="utf-8")
            episode = ImportedEpisode("S01E01", "Tập 1", video.name, video, manifest, "FULL_EPISODE", "en-US", 1)
            record = ProjectRecord.from_episode(
                episode,
                output_root=root / "recaps_da_render",
                voice_engine="kokoro",
                voice_id="af_heart",
                voice_style="film_recap",
                generate_srt=True,
                burn_subtitles=True,
                quality="high",
                use_gpu=True,
                encoder="h264_nvenc",
            )
            store = ProjectStore(root / "projects.json")
            store.save([record])
            loaded = store.load()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].episode_id, "S01E01")
            self.assertEqual(loaded[0].voice_id, "af_heart")
            self.assertEqual(loaded[0].encoder, "h264_nvenc")
            self.assertEqual(loaded[0].available_recap_modes, ["FULL_EPISODE"])
            self.assertEqual(loaded[0].output_counts_by_mode, {"FULL_EPISODE": 1})


if __name__ == "__main__":
    unittest.main()
