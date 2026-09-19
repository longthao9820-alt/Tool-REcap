from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

from .gpu import application_root
from .media import find_binary, run_command


LogFn = Callable[[str], None]
STT_PIPELINE_VERSION = "faster-whisper-large-v3-turbo-auto-v2"
REQUIRED_MODEL_FILES = ("model.bin", "config.json", "tokenizer.json")


class SpeechToTextError(RuntimeError):
    pass


def _model_directory(root: Path) -> Path:
    models = root / "runtime" / "speech" / "models"
    candidates = [models / "stt" / "faster-whisper-large-v3-turbo"]
    legacy_snapshots = (
        models
        / "qa-whisper"
        / "models--Systran--faster-whisper-small"
        / "snapshots"
    )
    candidates.extend(sorted(legacy_snapshots.glob("*"), reverse=True))
    for candidate in candidates:
        if candidate.is_dir() and all(
            (candidate / name).is_file() for name in REQUIRED_MODEL_FILES
        ):
            return candidate.resolve()
    raise SpeechToTextError(
        "Thiếu model Faster-Whisper multilingual large-v3-turbo để đọc lời thoại."
    )


def _python_runtime(root: Path) -> Path:
    # Speech recognition has a dedicated lightweight runtime. It must not
    # depend on a retired TTS engine and retain several GB of unrelated code.
    python = root / "runtime" / "speech" / "stt-runtime" / "Scripts" / "python.exe"
    if not python.is_file():
        raise SpeechToTextError("Thiếu runtime Faster-Whisper để đọc lời thoại.")
    return python.resolve()


def speech_to_text_status(root: Path | None = None) -> tuple[bool, str]:
    base = (root or application_root()).resolve()
    try:
        model = _model_directory(base)
        python = _python_runtime(base)
        worker = Path(__file__).with_name("stt_worker.py")
        if not worker.is_file():
            raise SpeechToTextError("Thiếu worker Faster-Whisper.")
    except SpeechToTextError as exc:
        return False, str(exc)
    return True, f"{model.name} / {python.parent.parent.name}"


def _cache_path(cache_dir: Path, model_dir: Path) -> Path:
    model = model_dir / "model.bin"
    stat = model.stat()
    identity = f"{STT_PIPELINE_VERSION}|{model_dir.name}|{stat.st_size}|{stat.st_mtime_ns}"
    tag = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return cache_dir / "speech-to-text" / f"transcript-{tag}.json"


def _valid_payload(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not isinstance(value.get("segments"), list):
        return None
    rows = []
    for item in value["segments"]:
        if not isinstance(item, dict):
            return None
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            return None
        raw_text = item.get("text")
        if not isinstance(raw_text, str):
            return None
        text = raw_text.strip()
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or not text:
            return None
        rows.append({**item, "start": start, "end": end, "text": text})
    return {**value, "segments": rows}


def _read_cache(path: Path) -> dict[str, Any] | None:
    try:
        return _valid_payload(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return None


def _run_worker(
    *,
    python: Path,
    worker: Path,
    model_dir: Path,
    audio: Path,
    cancel_event: threading.Event,
) -> dict[str, Any]:
    environment = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "_MEIPASS2"):
        environment.pop(key, None)
    environment["HF_HUB_OFFLINE"] = "1"
    environment["TRANSFORMERS_OFFLINE"] = "1"
    environment["PYTHONUTF8"] = "1"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0
    )
    process = subprocess.Popen(
        [str(python), str(worker), "--model", str(model_dir), "--audio", str(audio)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
        env=environment,
    )
    while process.poll() is None:
        if cancel_event.wait(0.2):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            raise SpeechToTextError("Đã dừng nhận dạng lời thoại.")
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        detail = (stderr or stdout).strip()[-1500:]
        raise SpeechToTextError("Không thể nhận dạng lời thoại: " + detail)
    try:
        payload = _valid_payload(json.loads(stdout))
    except json.JSONDecodeError as exc:
        raise SpeechToTextError("Faster-Whisper trả về dữ liệu không hợp lệ.") from exc
    if payload is None:
        raise SpeechToTextError("Faster-Whisper trả về transcript không hợp lệ.")
    return payload


def transcribe_video_audio(
    video: Path,
    cache_dir: Path,
    cancel_event: threading.Event,
    log: LogFn,
) -> list[dict[str, Any]]:
    """Create or load an auto-language, timestamped local speech transcript."""
    root = application_root()
    model_dir = _model_directory(root)
    python = _python_runtime(root)
    cache_path = _cache_path(cache_dir, model_dir)
    cached = _read_cache(cache_path) if cache_path.is_file() else None
    if cached is not None:
        log(f"{video.name}: dùng transcript lời thoại đã lưu ({len(cached['segments'])} đoạn).")
        return list(cached["segments"])

    work_dir = cache_dir / "speech-to-text"
    work_dir.mkdir(parents=True, exist_ok=True)
    audio = work_dir / "audio-mono-16k.wav"
    if not audio.is_file() or not audio.stat().st_size:
        log(f"{video.name}: đang trích âm thanh để nhận dạng lời thoại...")
        temporary_audio = audio.with_name(audio.stem + ".partial.wav")
        try:
            run_command(
                [
                    find_binary("ffmpeg"),
                    "-y",
                    "-i",
                    str(video),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(temporary_audio),
                ],
                cancel_event=cancel_event,
            )
            os.replace(temporary_audio, audio)
        finally:
            temporary_audio.unlink(missing_ok=True)

    log(f"{video.name}: đang nhận dạng lời thoại bằng Faster-Whisper (tự nhận diện ngôn ngữ)...")
    worker = Path(__file__).with_name("stt_worker.py")
    if not worker.is_file():
        raise SpeechToTextError("Thiếu worker Faster-Whisper để đọc lời thoại.")
    payload = _run_worker(
        python=python,
        worker=worker,
        model_dir=model_dir,
        audio=audio,
        cancel_event=cancel_event,
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(".partial")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, cache_path)
    language = str(payload.get("language") or "unknown")
    probability = float(payload.get("language_probability") or 0.0)
    log(
        f"{video.name}: nhận dạng được {len(payload['segments'])} đoạn lời thoại; "
        f"ngôn ngữ {language} ({probability:.0%}), thiết bị {payload.get('device', 'unknown')}."
    )
    return list(payload["segments"])
