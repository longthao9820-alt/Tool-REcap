from __future__ import annotations

import json
import threading
from pathlib import Path

from recap_tool import speech_to_text


def test_model_directory_requires_a_complete_local_snapshot(tmp_path) -> None:
    snapshots = (
        tmp_path
        / "runtime"
        / "speech"
        / "models"
        / "qa-whisper"
        / "models--Systran--faster-whisper-small"
        / "snapshots"
        / "snapshot"
    )
    snapshots.mkdir(parents=True)
    for name in speech_to_text.REQUIRED_MODEL_FILES:
        (snapshots / name).write_bytes(b"model")
    assert speech_to_text._model_directory(tmp_path) == snapshots.resolve()


def test_model_directory_prefers_production_large_v3_turbo(tmp_path) -> None:
    production = (
        tmp_path
        / "runtime"
        / "speech"
        / "models"
        / "stt"
        / "faster-whisper-large-v3-turbo"
    )
    production.mkdir(parents=True)
    for name in speech_to_text.REQUIRED_MODEL_FILES:
        (production / name).write_bytes(b"model")
    assert speech_to_text._model_directory(tmp_path) == production.resolve()


def test_transcript_cache_avoids_reextracting_and_retranscribing(tmp_path, monkeypatch) -> None:
    root = tmp_path / "app"
    model = (
        root
        / "runtime"
        / "speech"
        / "models"
        / "qa-whisper"
        / "models--Systran--faster-whisper-small"
        / "snapshots"
        / "snapshot"
    )
    model.mkdir(parents=True)
    for name in speech_to_text.REQUIRED_MODEL_FILES:
        (model / name).write_bytes(b"model")
    python = root / "runtime" / "speech" / "stt-runtime" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    video = tmp_path / "episode.mp4"
    video.write_bytes(b"video")
    cache = tmp_path / "cache"
    calls = {"extract": 0, "worker": 0}

    monkeypatch.setattr(speech_to_text, "application_root", lambda: root)

    def extract(args, **_kwargs):
        calls["extract"] += 1
        Path(args[-1]).write_bytes(b"wav")

    def worker(**_kwargs):
        calls["worker"] += 1
        return {
            "language": "ko",
            "language_probability": 0.98,
            "device": "cuda",
            "segments": [{"start": 1.0, "end": 2.0, "text": "안녕하세요"}],
        }

    monkeypatch.setattr(speech_to_text, "run_command", extract)
    monkeypatch.setattr(speech_to_text, "_run_worker", worker)
    first = speech_to_text.transcribe_video_audio(video, cache, threading.Event(), lambda _message: None)
    second = speech_to_text.transcribe_video_audio(video, cache, threading.Event(), lambda _message: None)

    assert first == second == [{"start": 1.0, "end": 2.0, "text": "안녕하세요"}]
    assert calls == {"extract": 1, "worker": 1}
    cached = list((cache / "speech-to-text").glob("transcript-*.json"))
    assert len(cached) == 1
    assert json.loads(cached[0].read_text(encoding="utf-8"))["language"] == "ko"
