from __future__ import annotations

import array
from dataclasses import replace
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from recap_tool.voice_system import (
    UnifiedTTSManager,
    VoiceRecord,
    VoiceSystemError,
    validate_voice_audio,
)


def write_pcm16_wav(path: Path, samples: list[int], *, sample_rate: int = 16000) -> None:
    values = array.array("h", samples)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(values.tobytes())


def make_voice() -> VoiceRecord:
    return VoiceRecord(
        voice_id="test.voice",
        display_name="Test voice",
        engine="test-engine",
        language="en-US",
        supported_languages=("en-US",),
        gender="female",
        styles=("neutral",),
        quality_tier="standard",
        installed=True,
        preview_available=True,
        commercial_use=True,
        personal_use_only=False,
        attribution_required=False,
        license="Test",
        source="test",
        model_version="test",
    )


class VoiceAudioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.voice = make_voice()

    def manager(self, root: Path, wav_bytes: bytes) -> tuple[UnifiedTTSManager, Path]:
        manager = UnifiedTTSManager(root)
        python = root / "python.exe"
        python.write_bytes(b"test")

        def worker(_engine: str, _python: Path, payload: dict[str, object]) -> dict[str, object]:
            Path(str(payload["output"])).write_bytes(wav_bytes)
            return {"ok": True}

        manager.voice = lambda _voice_id: self.voice  # type: ignore[method-assign]
        manager._manifest = lambda _engine: {"engine": "test-engine"}  # type: ignore[method-assign]
        manager._engine_python = lambda _manifest: python  # type: ignore[method-assign]
        manager._worker_call = worker  # type: ignore[method-assign]
        return manager, python

    def test_validate_accepts_pcm16_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "valid.wav"
            write_pcm16_wav(path, [2000] * 8000)
            validate_voice_audio(path)

    def test_validate_only_allows_full_scale_for_intermediate_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "full-scale.wav"
            write_pcm16_wav(path, [-32768, *([2000] * 7999)])
            with self.assertRaisesRegex(VoiceSystemError, "quá ngưỡng âm lượng"):
                validate_voice_audio(path)
            validate_voice_audio(path, allow_full_scale=True)

    def test_invalid_header_and_missing_file_report_voice_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.wav"
            with self.assertRaises(VoiceSystemError):
                validate_voice_audio(path)
            path.write_bytes(b"not a wav" * 100)
            with self.assertRaisesRegex(VoiceSystemError, "WAV không hợp lệ"):
                validate_voice_audio(path)

    def test_synthesize_rejects_truncated_worker_wav(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.wav"
            write_pcm16_wav(source, [2000] * 8000)
            truncated = source.read_bytes()[:144]
            manager, _python = self.manager(root, truncated)
            with self.assertRaisesRegex(VoiceSystemError, "thiếu dữ liệu hoặc bị cắt"):
                manager.synthesize(
                    text="Test content",
                    output_path=root / "output.wav",
                    voice_id=self.voice.voice_id,
                    language="en-US",
                    normalize=False,
                )

    def test_synthesize_rejects_silent_worker_wav(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            silent = root / "silent.wav"
            write_pcm16_wav(silent, [0] * 8000)
            manager, _python = self.manager(root, silent.read_bytes())
            with self.assertRaisesRegex(VoiceSystemError, "âm lượng quá nhỏ"):
                manager.synthesize(
                    text="Test content",
                    output_path=root / "output.wav",
                    voice_id=self.voice.voice_id,
                    language="en-US",
                    normalize=False,
                )

    def test_synthesize_normalizes_full_scale_worker_audio_before_strict_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "full-scale.wav"
            write_pcm16_wav(source, [-32768, *([2000] * 7999)])
            manager, _python = self.manager(root, source.read_bytes())

            def normalize(raw: Path, output: Path) -> None:
                validate_voice_audio(raw, allow_full_scale=True)
                write_pcm16_wav(output, [2000] * 8000)

            manager._normalize = normalize  # type: ignore[method-assign]
            output = manager.synthesize(
                text="Content with a full-scale peak",
                output_path=root / "output.wav",
                voice_id=self.voice.voice_id,
                language="en-US",
            )

            validate_voice_audio(output)
            self.assertFalse(output.with_suffix(".raw.wav").exists())

    def test_preview_regenerates_invalid_cached_wav(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = UnifiedTTSManager(root)
            cached = manager.catalog.preview_path(self.voice.voice_id, "en-US")
            cached.parent.mkdir(parents=True, exist_ok=True)
            write_pcm16_wav(cached, [0] * 8000)

            def regenerate(*, output_path: Path, **_kwargs: object) -> Path:
                write_pcm16_wav(output_path, [2000] * 8000)
                return output_path

            with (
                patch.object(manager, "voice", return_value=self.voice),
                patch.object(manager, "synthesize", side_effect=regenerate) as synthesize,
                patch.object(manager.catalog, "rebuild") as rebuild,
            ):
                result = manager.preview(self.voice.voice_id, "en-US")

            self.assertEqual(result, cached)
            synthesize.assert_called_once_with(
                text=manager.preview_text("en-US"),
                output_path=cached,
                voice_id=self.voice.voice_id,
                language="en-US",
                style="film_recap",
            )
            rebuild.assert_called_once_with()
            validate_voice_audio(cached)

    def test_preview_checks_language_before_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = UnifiedTTSManager(Path(temporary))
            with (
                patch.object(manager, "voice", return_value=self.voice),
                patch.object(manager, "synthesize") as synthesize,
            ):
                with self.assertRaisesRegex(VoiceSystemError, r"không hỗ trợ English \(UK\)"):
                    manager.preview(self.voice.voice_id, "en-GB")
            synthesize.assert_not_called()

    def test_voicestudio_synthesis_payload_is_anchored_to_selected_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            voice = replace(
                self.voice,
                voice_id="voicestudio.en.test",
                engine="voicestudio",
                language="en-US",
                supported_languages=("en-US",),
            )
            manager = UnifiedTTSManager(root)
            python = root / "python.exe"
            python.write_bytes(b"test")
            preview = manager.catalog.preview_path(voice.voice_id, "en-US")
            preview.parent.mkdir(parents=True, exist_ok=True)
            write_pcm16_wav(preview, [1800] * 8000)
            captured: list[dict[str, object]] = []

            def worker(_engine: str, _python: Path, payload: dict[str, object]) -> dict[str, object]:
                captured.append(payload)
                write_pcm16_wav(Path(str(payload["output"])), [2000] * 8000)
                return {"ok": True}

            manager.voice = lambda _voice_id: voice  # type: ignore[method-assign]
            manager._manifest = lambda _engine: {"engine": "voicestudio"}  # type: ignore[method-assign]
            manager._engine_python = lambda _manifest: python  # type: ignore[method-assign]
            manager._worker_call = worker  # type: ignore[method-assign]
            manager.synthesize(
                text="Target text",
                output_path=root / "output.wav",
                voice_id=voice.voice_id,
                language="en-US",
                normalize=False,
            )

            payload_voice = captured[0]["voice"]
            self.assertEqual(payload_voice["reference_audio"], str(preview.resolve()))
            self.assertEqual(payload_voice["reference_text"], manager.preview_text("en-US"))

    def test_voicestudio_cache_key_changes_when_selected_preview_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            voice = replace(
                self.voice,
                voice_id="voicestudio.en.test",
                engine="voicestudio",
                language="en-US",
                supported_languages=("en-US",),
            )
            manager = UnifiedTTSManager(root)
            manager.voice = lambda _voice_id: voice  # type: ignore[method-assign]
            preview = manager.catalog.preview_path(voice.voice_id, "en-US")
            preview.parent.mkdir(parents=True, exist_ok=True)
            write_pcm16_wav(preview, [1800] * 8000)
            first = manager.cache_key("Target text", voice.voice_id, "en-US", "film_recap")
            write_pcm16_wav(preview, [2200] * 8000)
            second = manager.cache_key("Target text", voice.voice_id, "en-US", "film_recap")
            self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
