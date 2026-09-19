from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from recap_tool.voice_worker import _local_hf_snapshot, _voicestudio_generation_kwargs
from recap_tool.voice_system import UnifiedTTSManager, VoiceCatalog, VoiceSystemError, normalize_tts_text


class UnifiedVoiceSystemTests(unittest.TestCase):
    def test_stable_legacy_voice_id_migration(self) -> None:
        manager = UnifiedTTSManager()
        self.assertEqual(manager.migrate_voice_id("M1"), "M1")
        self.assertEqual(manager.migrate_voice_id("af_heart"), "af_heart")
        self.assertEqual(manager.migrate_voice_id("vieneu.thai_son"), "vieneu.thai_son")
        self.assertEqual(manager.migrate_voice_id("piper.de.mls_2422"), "piper.de.mls_2422")
        self.assertEqual(manager.migrate_voice_id("piper.de.mls_10148"), "piper.de.mls_10148")

    def test_failed_german_mls_previews_are_hidden(self) -> None:
        voices = UnifiedTTSManager().list_voices("de-DE")
        self.assertFalse(any(item.voice_id.startswith("piper.de.mls_") for item in voices))
        self.assertFalse(any(item.voice_id.startswith("f5tts.de.") for item in voices))
        self.assertFalse(any(item.gender == "neutral" for item in voices))
        with self.assertRaisesRegex(VoiceSystemError, "đã bị loại"):
            UnifiedTTSManager().voice("piper.de.mls_2422")

    def test_cache_key_changes_with_style_and_model_version(self) -> None:
        manager = UnifiedTTSManager()
        first = manager.cache_key("hello", "voicestudio.en.anchor", "en-US", "neutral")
        second = manager.cache_key("hello", "voicestudio.en.anchor", "en-US", "film_recap")
        self.assertNotEqual(first, second)

    def test_voicestudio_uses_preview_as_clone_reference(self) -> None:
        request = {
            "text": "A new target sentence.",
            "language": "en-US",
            "voice": {
                "reference_audio": "selected-preview.wav",
                "reference_text": "Welcome to the test.",
                "voice_instruction": "female, elderly, low pitch",
            },
        }
        kwargs = _voicestudio_generation_kwargs(request)
        self.assertEqual(kwargs["ref_audio"], "selected-preview.wav")
        self.assertEqual(kwargs["ref_text"], "Welcome to the test.")
        self.assertNotIn("instruct", kwargs)

    def test_catalog_contains_native_voices_for_each_required_language(self) -> None:
        voices = VoiceCatalog().rebuild()
        for language in ("en-US", "en-GB"):
            native = [item for item in voices if item.language == language and item.native_language and item.installed]
            self.assertGreaterEqual(len(native), 1, language)
        self.assertFalse(any(item.gender == "neutral" for item in voices))

    def test_favorites_and_recent_are_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = VoiceCatalog(root)
            catalog.state_path.parent.mkdir(parents=True, exist_ok=True)
            catalog.set_favorite("voice.one", True)
            catalog.record_recent("voice.one")
            self.assertEqual(catalog.favorites(), {"voice.one"})
            self.assertEqual(catalog.recent()[0], "voice.one")

    def test_catalog_reloads_manifest_and_ignores_stale_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            catalog = VoiceCatalog(Path(temporary))
            engine = catalog.runtime / "engines" / "voicestudio"
            engine.mkdir(parents=True)
            manifest = engine / "provider.json"
            voices = [
                {"voice_id": name, "display_name": name, "language": "en-US", "gender": gender, **flags}
                for name, gender, flags in [
                    ("voicestudio.en.good", "female", {}),
                    ("voicestudio.en.neutral", "neutral", {}),
                    ("voicestudio.en.failed", "male", {"quality_status": "failed"}),
                    ("voicestudio.en.disabled", "male", {"enabled": False}),
                    ("voicestudio.en.delivery_neutral", "male", {}),
                    ("voicestudio.en.missing", "male", {"reference_audio": "missing.wav"}),
                ]
            ]
            manifest.write_text(json.dumps({"engine": "voicestudio", "voices": voices}), encoding="utf-8")
            self.assertEqual([item.voice_id for item in catalog.rebuild()], ["voicestudio.en.good"])
            manifest.write_text(json.dumps({"engine": "voicestudio", "enabled": False, "voices": voices}), encoding="utf-8")
            self.assertEqual(catalog.load(), [])

    def test_release_executable_uses_shared_runtime(self) -> None:
        from recap_tool.voice_system import application_root
        from recap_tool.gpu import application_root as gpu_root

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ffmpeg = root / "runtime" / "ffmpeg" / "bin" / "ffmpeg.exe"
            ffmpeg.parent.mkdir(parents=True)
            ffmpeg.touch()
            with patch("sys.frozen", True, create=True), patch("sys.executable", str(root / "release" / "RecapStudio.exe")):
                self.assertEqual(application_root(), root.resolve())
                self.assertEqual(gpu_root(), root.resolve())

    def test_retired_voices_cannot_return_when_manifest_is_enabled(self) -> None:
        for voice_id in ("kokoro.af_heart", "chatterbox.default"):
            gender = "neutral" if voice_id == "chatterbox.default" else "male"
            self.assertFalse(VoiceCatalog._is_usable_item({"voice_id": voice_id, "gender": gender, "enabled": True}))

    def test_every_installed_native_voice_has_preview(self) -> None:
        catalog = VoiceCatalog()
        missing = [item.voice_id for item in catalog.rebuild() if item.installed and item.native_language and not catalog.preview_path(item.voice_id, item.language).is_file()]
        self.assertEqual(missing, [])

    def test_renamed_thorsten_neutral_is_still_retired(self) -> None:
        self.assertFalse(VoiceCatalog._is_usable_item({
            "voice_id": "piper.new_de.thorsten_emotional", "gender": "male",
            "technical_voice_id": "thorsten_emotional:4", "enabled": True,
        }))

    def test_replacement_set_matches_delivered_voices(self) -> None:
        voices = UnifiedTTSManager().list_voices(installed_only=True)
        replacements = {voice.voice_id for voice in voices}
        self.assertEqual(len(replacements), 12)
        self.assertEqual(sum(voice.engine == "voicestudio" for voice in voices), 12)
        self.assertTrue({
            "voicestudio.en.librarian", "voicestudio.en.documentarian",
            "voicestudio.en.anchor", "voicestudio.en.commentator",
        }.issubset(replacements))

    def test_text_normalization_is_language_specific(self) -> None:
        self.assertIn("season 1, episode 3", normalize_tts_text("S01E03", "en-US"))
        self.assertIn("series 1, episode 3", normalize_tts_text("S01E03", "en-GB"))

    def test_local_hf_snapshot_ignores_incomplete_download(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            model_dir = Path(temporary)
            snapshots = model_dir / "hf-cache" / "hub" / "models--owner--model" / "snapshots"
            complete = snapshots / "complete"
            incomplete = snapshots / "incomplete"
            complete.mkdir(parents=True)
            incomplete.mkdir()
            (complete / "config.json").write_text("{}", encoding="utf-8")
            self.assertEqual(
                _local_hf_snapshot(model_dir, "models--owner--model", ("config.json",)),
                complete,
            )


if __name__ == "__main__":
    unittest.main()
