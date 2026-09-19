from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from recap_tool.audio_preview import AudioPreviewError, AudioPreviewPlayer


class AudioPreviewTests(unittest.TestCase):
    def test_rejects_missing_audio(self) -> None:
        with self.assertRaises(AudioPreviewError):
            AudioPreviewPlayer.play(Path(tempfile.gettempdir()) / "missing-preview.wav")

    @unittest.skipUnless(os.name == "nt", "winsound chỉ có trên Windows")
    def test_plays_wav_asynchronously_without_external_process(self) -> None:
        path = Path(tempfile.mkdtemp()) / "preview.wav"
        path.write_bytes(b"RIFF" + b"\x00" * 128)
        with patch("winsound.PlaySound") as play_sound:
            AudioPreviewPlayer.play(path)
            self.assertEqual(play_sound.call_count, 2)
            self.assertEqual(AudioPreviewPlayer.current(), path.resolve())
            AudioPreviewPlayer.stop()


if __name__ == "__main__":
    unittest.main()
