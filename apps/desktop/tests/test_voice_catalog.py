from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from recap_tool.tts import LocalSpeechClient, load_voice_catalog
from recap_tool.voice_system import VoiceSystemError


class VoiceCatalogTests(unittest.TestCase):
    def test_catalog_has_all_required_languages(self) -> None:
        voices = load_voice_catalog()
        for language in ("en-US", "en-GB"):
            self.assertGreaterEqual(sum(language in item.languages for item in voices), 1)
        self.assertEqual({language for voice in voices for language in voice.languages}, {"en-US", "en-GB"})

    def test_catalog_has_all_integrated_engines(self) -> None:
        voices = load_voice_catalog()
        self.assertEqual({item.engine for item in voices}, {"voicestudio"})
        self.assertEqual(len(voices), 12)
        self.assertTrue(all(item.voice_id.startswith("voicestudio.en.") for item in voices))
        self.assertFalse(any(item.gender == "neutral" for item in voices))

    def test_every_voice_has_license_and_source(self) -> None:
        for voice in load_voice_catalog():
            self.assertTrue(voice.license_name)
            self.assertTrue(voice.source_url.startswith("https://"))

    def test_runtime_status_reports_missing_components(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            status = LocalSpeechClient(Path(temporary)).runtime_status()
        self.assertFalse(status["ready"])
        self.assertIn("voice_engines", status["missing"])

    def test_compatible_voices_filters_language(self) -> None:
        american = LocalSpeechClient.compatible_voices("en-US")
        british = LocalSpeechClient.compatible_voices("en-GB")
        self.assertEqual(len(american), 6)
        self.assertEqual(len(british), 6)
        self.assertEqual(LocalSpeechClient.compatible_voices("de-DE"), [])

    def test_preview_sentence_is_localized(self) -> None:
        self.assertIn("Welcome", LocalSpeechClient.preview_text("en-US"))
        self.assertIn("Welcome", LocalSpeechClient.preview_text("en-GB"))
        with self.assertRaises(VoiceSystemError):
            LocalSpeechClient.preview_text("vi-VN")


if __name__ == "__main__":
    unittest.main()
