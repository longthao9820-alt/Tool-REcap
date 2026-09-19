from __future__ import annotations

import hashlib
import atexit
import array
import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import wave


LANGUAGE_NAMES = {
    "en-US": "English (US)",
    "en-GB": "English (UK)",
}

PRODUCTION_VOICE_ENGINE = "voicestudio"
PRODUCTION_VOICE_LANGUAGES = frozenset(LANGUAGE_NAMES)

STYLE_NAMES = {
    "storytelling": "Kể chuyện",
    "film_recap": "Recap phim",
    "documentary": "Documentary",
    "crime_thriller": "Crime / Thriller",
    "drama": "Drama",
    "soap_emotional": "Soap / Emotional",
    "energetic": "Năng động",
}

STYLE_INSTRUCTIONS = {
    "storytelling": "Speak naturally and clearly, like an engaging storyteller, at a moderate pace.",
    "film_recap": "Narrate a film recap clearly and naturally, engaging but not theatrical, with crisp articulation and a moderate pace.",
    "documentary": "Use a calm, authoritative documentary narration with measured pacing and clear articulation.",
    "crime_thriller": "Use a controlled serious tone with subtle tension, clear words, and no exaggerated acting.",
    "drama": "Use an emotionally aware dramatic narration, restrained and natural rather than theatrical.",
    "soap_emotional": "Use a warm natural emotional tone suitable for a television soap recap, without melodrama.",
    "neutral": "Use a neutral, clear, steady delivery.",
    "energetic": "Use an energetic and engaging delivery while keeping every word clear.",
}

# Include the post-processing contract in cache identities so audio produced by
# an older pipeline is never silently reused after the contract changes.
VOICE_AUDIO_PIPELINE_VERSION = "pcm16-mono48k-loudnorm-v2"


def normalize_tts_text(text: str, language: str) -> str:
    """Chuẩn hóa bản đọc nội bộ mà không sửa narration_text trong JSON."""
    value = " ".join(text.replace("\u00a0", " ").split())
    labels = {
        "en-US": ("season", "episode"),
        "en-GB": ("series", "episode"),
    }
    season, episode = labels.get(language, ("season", "episode"))
    value = re.sub(r"\bS(\d{1,2})E(\d{1,3})\b", lambda match: f"{season} {int(match.group(1))}, {episode} {int(match.group(2))}", value, flags=re.IGNORECASE)
    if language == "en-US":
        value = re.sub(r"\bMr\.", "Mister", value)
        value = re.sub(r"\bDr\.", "Doctor", value)
    return value


class VoiceSystemError(RuntimeError):
    pass


def validate_voice_audio(path: str | Path, *, allow_full_scale: bool = False) -> None:
    """Validate a PCM16 voice file.

    Engine output is allowed to touch full scale because it is only an
    intermediate file and is normalized before use. Published previews,
    render inputs and cached files keep the stricter headroom requirement.
    """
    audio_path = Path(path).expanduser()
    if not audio_path.is_file():
        raise VoiceSystemError(f"Không tìm thấy tệp WAV: {audio_path}")

    try:
        with wave.open(str(audio_path), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            frame_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            if wav_file.getcomptype() != "NONE" or sample_width != 2:
                raise VoiceSystemError("WAV phải là PCM16 không nén.")
            if channels <= 0 or frame_rate <= 0 or frame_count <= 0:
                raise VoiceSystemError("WAV có thông số PCM không hợp lệ.")
            if frame_count * 10 < frame_rate * 3:
                raise VoiceSystemError("WAV phải có thời lượng tối thiểu 0,3 giây.")

            remaining_frames = frame_count
            sample_count = 0
            sum_squares = 0
            peak = 0
            while remaining_frames:
                chunk_frames = min(8192, remaining_frames)
                chunk = wav_file.readframes(chunk_frames)
                expected_bytes = chunk_frames * channels * sample_width
                if len(chunk) != expected_bytes:
                    raise VoiceSystemError("WAV bị thiếu dữ liệu hoặc bị cắt.")
                samples = array.array("h")
                samples.frombytes(chunk)
                if sys.byteorder != "little":
                    samples.byteswap()
                if len(samples) != chunk_frames * channels:
                    raise VoiceSystemError("WAV bị thiếu dữ liệu hoặc bị cắt.")
                for sample in samples:
                    absolute = abs(sample)
                    peak = max(peak, absolute)
                    sum_squares += sample * sample
                sample_count += len(samples)
                remaining_frames -= chunk_frames

            rms = (sum_squares / sample_count) ** 0.5 / 32768.0
            if rms < 0.003:
                raise VoiceSystemError("WAV có âm lượng quá nhỏ (RMS dưới 0,003).")
            if not allow_full_scale and peak / 32768.0 >= 1.0:
                raise VoiceSystemError("WAV bị quá ngưỡng âm lượng (peak phải nhỏ hơn 1).")
    except VoiceSystemError:
        raise
    except (OSError, EOFError, ValueError, OverflowError, wave.Error) as exc:
        raise VoiceSystemError(f"WAV không hợp lệ hoặc bị thiếu dữ liệu: {audio_path}") from exc


@dataclass(frozen=True)
class VoiceRecord:
    voice_id: str
    display_name: str
    engine: str
    language: str
    supported_languages: tuple[str, ...]
    gender: str
    styles: tuple[str, ...]
    quality_tier: str
    installed: bool
    preview_available: bool
    commercial_use: bool
    personal_use_only: bool
    attribution_required: bool
    license: str
    source: str
    model_version: str
    native_language: bool = True
    style_control: bool = False
    description: str = ""
    technical_voice_id: str = ""
    reference_audio: str = ""
    reference_text: str = ""
    voice_instruction: str = ""

    @property
    def label(self) -> str:
        gender = {"male": "Nam", "female": "Nữ", "neutral": "Trung tính"}.get(self.gender, self.gender)
        tier = "Premium Local" if self.quality_tier == "premium_local" else "Standard"
        if "—" in self.display_name:
            return f"{self.display_name} — {tier}"
        return f"{self.display_name} — {gender} — {tier}"


@dataclass(frozen=True)
class EngineHealth:
    engine: str
    status: str
    message: str
    installed: bool
    ready: bool


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
        if root.name == "release" and (root.parent / "runtime" / "ffmpeg" / "bin" / "ffmpeg.exe").is_file():
            return root.parent
        return root
    return Path(__file__).resolve().parents[3]


class VoiceCatalog:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or application_root()).resolve()
        self.runtime = self.root / "runtime" / "speech"
        self.path = self.runtime / "catalog" / "voice_catalog.v3.json"
        self.state_path = self.runtime / "catalog" / "user_voice_state.json"
        self._lock = threading.RLock()

    def load(self) -> list[VoiceRecord]:
        with self._lock:
            return self.rebuild(persist=False)

    @staticmethod
    def _is_usable_item(item: dict[str, Any]) -> bool:
        """Ẩn giọng trung tính hoặc đã bị tắt khỏi mọi catalog cũ/mới."""
        return (
            item.get("enabled") is not False
            and item.get("gender") in {"male", "female"}
            and item.get("quality_status") != "failed"
            and not str(item.get("voice_id", "")).endswith("_neutral")
            and not str(item.get("voice_id", "")).startswith(("kokoro.a", "kokoro.b"))
            # Renaming a retired Thorsten preset must not bypass curation.
            and not str(item.get("technical_voice_id", "")).startswith("thorsten")
            and "thorsten" not in str(item.get("reference_audio", "")).casefold()
        )

    def rebuild(self, *, persist: bool = True) -> list[VoiceRecord]:
        records: list[VoiceRecord] = []
        manifests = sorted((self.runtime / "engines").glob("*/provider.json"))
        for manifest in manifests:
            try:
                raw = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if raw.get("enabled") is False:
                continue
            if raw.get("engine") != PRODUCTION_VOICE_ENGINE:
                continue
            installed = self._engine_installed(raw)
            disabled_prefixes = tuple(str(value) for value in raw.get("disabled_voice_prefixes", []))
            for item in raw.get("voices", []):
                voice_id = str(item["voice_id"])
                if not self._is_usable_item(item) or voice_id.startswith(disabled_prefixes):
                    continue
                language = str(item["language"])
                if language not in PRODUCTION_VOICE_LANGUAGES or not voice_id.startswith("voicestudio.en."):
                    continue
                preview = self.preview_path(voice_id, language)
                reference = str(item.get("reference_audio") or "")
                if reference and not (self.root / reference).is_file():
                    continue
                if raw["engine"] == "piper" and not (self.root / (reference + ".json")).is_file():
                    continue
                records.append(
                    VoiceRecord(
                        voice_id=voice_id,
                        display_name=str(item["display_name"]),
                        engine=str(raw["engine"]),
                        language=language,
                        supported_languages=tuple(item.get("supported_languages") or [language]),
                        gender=str(item.get("gender") or "neutral"),
                        styles=tuple(style for style in (item.get("styles") or raw.get("styles") or ["film_recap"]) if style != "neutral"),
                        quality_tier=str(item.get("quality_tier") or raw.get("quality_tier") or "standard"),
                        installed=installed,
                        preview_available=preview.is_file(),
                        commercial_use=bool(item.get("commercial_use", raw.get("commercial_use", False))),
                        personal_use_only=bool(item.get("personal_use_only", raw.get("personal_use_only", False))),
                        attribution_required=bool(item.get("attribution_required", raw.get("attribution_required", False))),
                        license=str(item.get("license") or raw.get("license") or "Unknown"),
                        source=str(item.get("source") or raw.get("source") or ""),
                        model_version=str(item.get("model_version") or raw.get("model_version") or "unknown"),
                        native_language=bool(item.get("native_language", True)),
                        style_control=bool(item.get("style_control", raw.get("style_control", False))),
                        description=str(item.get("description") or ""),
                        technical_voice_id=str(item.get("technical_voice_id") or item["voice_id"].split(".")[-1]),
                        reference_audio=str(item.get("reference_audio") or ""),
                        reference_text=str(item.get("reference_text") or ""),
                        voice_instruction=str(item.get("voice_instruction") or ""),
                    )
                )
        records.sort(key=lambda item: (item.language, not item.native_language, item.quality_tier != "premium_local", item.display_name.casefold()))
        if not persist:
            return records
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".partial")
        temporary.write_text(json.dumps({"catalog_version": "4.0-curated", "voices": [asdict(item) for item in records]}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)
        return records

    def state(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def set_favorite(self, voice_id: str, favorite: bool) -> None:
        state = self.state()
        favorites = set(state.get("favorites") or [])
        favorites.add(voice_id) if favorite else favorites.discard(voice_id)
        state["favorites"] = sorted(favorites)
        self._save_state(state)

    def record_recent(self, voice_id: str) -> None:
        state = self.state()
        recent = [item for item in state.get("recent") or [] if item != voice_id]
        state["recent"] = [voice_id, *recent][:10]
        self._save_state(state)

    def favorites(self) -> set[str]:
        return set(self.state().get("favorites") or [])

    def recent(self) -> list[str]:
        return list(self.state().get("recent") or [])

    def preview_path(self, voice_id: str, language: str) -> Path:
        safe = voice_id.replace(".", "-").replace("/", "-")
        return self.runtime / "previews" / language / f"{safe}.wav"

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".partial")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.state_path)

    def _engine_installed(self, manifest: dict[str, Any]) -> bool:
        relative = str(manifest.get("python") or ".venv/Scripts/python.exe")
        engine_dir = self.runtime / "engines" / str(manifest["engine"])
        if not (engine_dir / relative).is_file():
            return False
        marker = str(manifest.get("ready_marker") or "")
        return not marker or (self.runtime / "models" / str(manifest["engine"]) / marker).is_file()


class UnifiedTTSManager:
    _workers: dict[str, subprocess.Popen[str]] = {}
    _worker_locks: dict[str, threading.RLock] = {}
    _worker_logs: dict[str, Any] = {}
    _pool_lock = threading.RLock()

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or application_root()).resolve()
        self.runtime = self.root / "runtime" / "speech"
        self.catalog = VoiceCatalog(self.root)
        self._lock = threading.RLock()

    def list_voices(self, language: str | None = None, *, installed_only: bool = False) -> list[VoiceRecord]:
        voices = self.catalog.load()
        if language:
            voices = [item for item in voices if language in item.supported_languages]
        if installed_only:
            voices = [item for item in voices if item.installed]
        return voices

    def voice(self, voice_id: str) -> VoiceRecord:
        migrated = self.migrate_voice_id(voice_id)
        record = next((item for item in self.catalog.load() if item.voice_id == migrated), None)
        if not record:
            raise VoiceSystemError(
                f"Giọng {voice_id} đã bị loại hoặc chưa được cài. "
                "Hãy chọn một giọng VoiceStudio tiếng Anh trong Thư viện giọng."
            )
        return record

    def migrate_voice_id(self, voice_id: str) -> str:
        # Retired IDs intentionally stay unresolved so old projects require an
        # explicit VoiceStudio English selection instead of silently changing
        # their narrator.
        return voice_id

    def health_check(self, engine: str, *, synthesize: bool = False) -> EngineHealth:
        manifest = self._manifest(engine)
        available = [item for item in self.list_voices(installed_only=True) if item.engine == engine]
        if not available:
            return EngineHealth(engine, "NOT_AVAILABLE", "Không còn giọng hợp lệ đã cài trong engine này.", False, False)
        python = self._engine_python(manifest)
        if not python.is_file():
            return EngineHealth(engine, "NOT_INSTALLED", "Engine chưa được cài.", False, False)
        probe = subprocess.run([str(python), "-c", str(manifest.get("health_import") or "print('ok')")], capture_output=True, text=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if probe.returncode != 0:
            return EngineHealth(engine, "ERROR", (probe.stderr or probe.stdout).strip()[:300], True, False)
        if synthesize:
            voice = next((item for item in self.list_voices(installed_only=True) if item.engine == engine and (item.commercial_use or item.personal_use_only)), None)
            if voice:
                test = self.runtime / "health" / f"{engine}.wav"
                try:
                    self.synthesize(text=self.preview_text(voice.language), output_path=test, voice_id=voice.voice_id, language=voice.language, style="neutral", normalize=True)
                except Exception as exc:
                    return EngineHealth(engine, "ERROR", str(exc)[:300], True, False)
        return EngineHealth(engine, "READY", "Engine sẵn sàng.", True, True)

    def install_engine(self, engine: str) -> EngineHealth:
        manifest = self._manifest(engine)
        python = self._engine_python(manifest)
        if not python.is_file():
            return EngineHealth(engine, "ERROR", "Thiếu runtime Python của engine.", False, False)
        candidate = next((item for item in self.catalog.rebuild() if item.engine == engine and (item.commercial_use or item.personal_use_only)), None)
        if not candidate:
            return EngineHealth(engine, "ERROR", "Engine chưa có giọng production hợp lệ.", True, False)
        test = self.runtime / "health" / f"{engine}.wav"
        try:
            self.synthesize(text=self.preview_text(candidate.language), output_path=test, voice_id=candidate.voice_id, language=candidate.language, style="neutral")
            marker = str(manifest.get("ready_marker") or "")
            if marker:
                ready = self.runtime / "models" / engine / marker
                ready.parent.mkdir(parents=True, exist_ok=True)
                ready.write_text("ready\n", encoding="utf-8")
            self.catalog.rebuild()
            return EngineHealth(engine, "READY", "Đã tải model và tạo WAV kiểm tra.", True, True)
        except Exception as exc:
            return EngineHealth(engine, "ERROR", str(exc)[:500], True, False)

    def synthesize(self, *, text: str, output_path: str | Path, voice_id: str, language: str, style: str = "film_recap", normalize: bool = True) -> Path:
        voice = self.voice(voice_id)
        if language not in voice.supported_languages:
            raise VoiceSystemError(f"Giọng {voice.display_name} không hỗ trợ {LANGUAGE_NAMES.get(language, language)}.")
        if not voice.commercial_use and not voice.personal_use_only:
            raise VoiceSystemError("Giọng này chưa có phạm vi sử dụng hợp lệ.")
        manifest = self._manifest(voice.engine)
        python = self._engine_python(manifest)
        if not python.is_file():
            raise VoiceSystemError(f"Engine {voice.engine} chưa được cài.")
        output = Path(output_path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        raw = output.with_suffix(".raw.wav") if normalize else output
        voice_payload = asdict(voice)
        voice_anchor = self._ensure_voice_anchor(voice, language, style, output)
        if voice_anchor is not None:
            voice_payload["reference_audio"] = str(voice_anchor)
            voice_payload["reference_text"] = self.preview_text(language)
        elif voice.reference_audio:
            reference = Path(voice.reference_audio)
            voice_payload["reference_audio"] = str(reference if reference.is_absolute() else self.root / reference)
        stable_seed = int(hashlib.sha256(f"{text}|{voice.voice_id}|{language}|{style}|{voice.model_version}".encode("utf-8")).hexdigest()[:8], 16)
        payload = {"text": normalize_tts_text(text, language), "original_text": text, "output": str(raw), "language": language, "style": style, "style_instruction": STYLE_INSTRUCTIONS.get(style, STYLE_INSTRUCTIONS["neutral"]), "seed": stable_seed, "voice": voice_payload, "model_dir": str(self.runtime / "models" / voice.engine), "engine_config": manifest}
        response = self._worker_call(voice.engine, python, payload)
        if not response.get("ok") or not raw.is_file():
            raise VoiceSystemError(f"Engine {voice.engine} không thể tạo giọng: {str(response.get('error') or 'Không có WAV đầu ra')[:1200]}")
        if normalize:
            # Some TTS engines legitimately quantize a negative sample to
            # -32768. The raw file is not a render input yet: validate its
            # structure/signal first, normalize it, then enforce headroom on
            # the actual output. Rejecting it before loudnorm made every
            # episode containing such a sample fail unnecessarily.
            validate_voice_audio(raw, allow_full_scale=True)
            self._normalize(raw, output)
            validate_voice_audio(output)
            raw.unlink(missing_ok=True)
        else:
            validate_voice_audio(raw)
        self.catalog.record_recent(voice.voice_id)
        return output

    def preview(self, voice_id: str, language: str, style: str = "film_recap") -> Path:
        voice = self.voice(voice_id)
        if language not in voice.supported_languages:
            raise VoiceSystemError(f"Giọng {voice.display_name} không hỗ trợ {LANGUAGE_NAMES.get(language, language)}.")
        path = self.catalog.preview_path(voice.voice_id, language)
        if path.is_file():
            try:
                validate_voice_audio(path)
            except VoiceSystemError:
                path.unlink(missing_ok=True)
            else:
                return path
        self.synthesize(text=self.preview_text(language), output_path=path, voice_id=voice.voice_id, language=language, style=style)
        self.catalog.rebuild()
        return path

    @staticmethod
    def preview_text(language: str) -> str:
        texts = {
            "en-US": "Welcome to Tool Recap. This voice was generated by artificial intelligence.",
            "en-GB": "Welcome to Tool Recap. This voice was generated by artificial intelligence.",
        }
        if language not in texts:
            raise VoiceSystemError(f"Không có câu nghe thử cho {language}.")
        return texts[language]

    def cache_key(self, text: str, voice_id: str, language: str, style: str) -> str:
        voice = self.voice(voice_id)
        identity = "native"
        if voice.engine == "voicestudio":
            anchor = self._ensure_voice_anchor(voice, language, style, None)
            if anchor is not None:
                identity = "preview-clone-v1:" + hashlib.sha256(anchor.read_bytes()).hexdigest()
        value = "\n".join(
            (
                text,
                voice.voice_id,
                language,
                style,
                voice.model_version,
                "speed=1.0",
                identity,
                VOICE_AUDIO_PIPELINE_VERSION,
            )
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _ensure_voice_anchor(
        self,
        voice: VoiceRecord,
        language: str,
        style: str,
        output: Path | None,
    ) -> Path | None:
        """Dùng đúng mẫu nghe thử làm danh tính cố định cho preset VoiceStudio."""
        if voice.engine != "voicestudio":
            return None
        anchor = self.catalog.preview_path(voice.voice_id, language).resolve()
        if output is not None and output.resolve() == anchor:
            return None
        if anchor.is_file():
            try:
                validate_voice_audio(anchor)
            except VoiceSystemError:
                anchor.unlink(missing_ok=True)
        if not anchor.is_file():
            self.synthesize(
                text=self.preview_text(language),
                output_path=anchor,
                voice_id=voice.voice_id,
                language=language,
                style=style,
            )
        return anchor

    def _manifest(self, engine: str) -> dict[str, Any]:
        path = self.runtime / "engines" / engine / "provider.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VoiceSystemError(f"Thiếu cấu hình engine {engine}.") from exc

    def _engine_python(self, manifest: dict[str, Any]) -> Path:
        return self.runtime / "engines" / str(manifest["engine"]) / str(manifest.get("python") or ".venv/Scripts/python.exe")

    def _normalize(self, source: Path, output: Path) -> None:
        ffmpeg = self.root / "runtime" / "ffmpeg" / "bin" / "ffmpeg.exe"
        if not ffmpeg.is_file():
            raise VoiceSystemError("Không tìm thấy FFmpeg để chuẩn hóa âm thanh.")
        result = subprocess.run([str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(output)], capture_output=True, text=True, timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0 or not output.is_file():
            raise VoiceSystemError("Không thể chuẩn hóa WAV: " + (result.stderr or "")[:500])

    def _worker_call(self, engine: str, python: Path, payload: dict[str, Any]) -> dict[str, Any]:
        with self._pool_lock:
            lock = self._worker_locks.setdefault(engine, threading.RLock())
        with lock:
            for attempt in range(2):
                process = self._ensure_worker(engine, python)
                try:
                    assert process.stdin is not None and process.stdout is not None
                    process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                    process.stdin.flush()
                    line = process.stdout.readline()
                    if not line:
                        raise RuntimeError("Engine đã dừng đột ngột.")
                    return json.loads(line)
                except Exception:
                    self.shutdown_engine(engine)
                    if attempt:
                        raise
            raise VoiceSystemError(f"Không thể giao tiếp với engine {engine}.")

    def _ensure_worker(self, engine: str, python: Path) -> subprocess.Popen[str]:
        with self._pool_lock:
            current = self._workers.get(engine)
            if current is not None and current.poll() is None:
                return current
            if engine in {"qwen", "chatterbox", "vieneu", "f5tts", "voicestudio"}:
                for other in list(self._workers):
                    if other in {"qwen", "chatterbox", "vieneu", "f5tts", "voicestudio"} and other != engine:
                        self.shutdown_engine(other)
            log_dir = self.runtime / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log = (log_dir / f"{engine}.log").open("a", encoding="utf-8")
            environment = os.environ.copy()
            for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "_MEIPASS2"):
                environment.pop(key, None)
            environment["HF_HOME"] = str(self.runtime / "models" / engine / "hf-cache")
            environment["PYTHONUTF8"] = "1"
            environment["PYTHONIOENCODING"] = "utf-8"
            if (self.runtime / "models" / engine / ".ready").is_file():
                environment["HF_HUB_OFFLINE"] = "1"
                environment["TRANSFORMERS_OFFLINE"] = "1"
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
            worker = Path(__file__).with_name("voice_worker.py")
            process = subprocess.Popen([str(python), str(worker), "--engine", engine, "--serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=flags, env=environment)
            self._workers[engine] = process
            self._worker_logs[engine] = log
            return process

    @classmethod
    def shutdown_engine(cls, engine: str) -> None:
        with cls._pool_lock:
            process = cls._workers.pop(engine, None)
            log = cls._worker_logs.pop(engine, None)
        if process is not None and process.poll() is None:
            try:
                assert process.stdin is not None
                process.stdin.write("__quit__\n")
                process.stdin.flush()
                process.wait(timeout=8)
            except Exception:
                process.kill()
        if log is not None:
            log.close()

    @classmethod
    def shutdown_all(cls) -> None:
        for engine in list(cls._workers):
            cls.shutdown_engine(engine)


atexit.register(UnifiedTTSManager.shutdown_all)
