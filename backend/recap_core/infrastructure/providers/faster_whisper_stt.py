"""Production-shaped local Faster-Whisper STT adapter.

This milestone is LOCAL_ONLY: the adapter must never fetch weights. It therefore
requires an explicit, already-present local model directory and loads it with
`local_files_only=True`. A bare model name (which would make faster-whisper
resolve and download from a hub) is rejected before any model object is built, so
capability probing cannot become a silent download.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from ...domain.errors import ProviderOutputInvalidError, ProviderUnavailableError
from ...ports.stt import ProviderCapability, TranscriptDTO, parse_transcript_dto

PROVIDER_NAME = "faster-whisper"
PROVIDER_VERSION = "faster-whisper-local-1"

#: Files ctranslate2 needs before a Whisper directory can be loaded offline.
REQUIRED_MODEL_FILES: tuple[str, ...] = ("model.bin", "config.json")


def _module() -> Any:
    """Import faster_whisper lazily so the package stays an optional dependency."""
    try:
        import faster_whisper  # noqa: PLC0415 - optional heavy dependency
    except ImportError as exc:  # pragma: no cover - exercised via capability()
        raise ProviderUnavailableError(f"faster-whisper is not installed: {exc}") from exc
    return faster_whisper


def missing_model_files(model_dir: Path) -> tuple[str, ...]:
    """Names of required weights that are absent from a local model directory."""
    return tuple(name for name in REQUIRED_MODEL_FILES if not (model_dir / name).is_file())


class LocalFasterWhisperProvider:
    """`STTProvider` over a locally present Faster-Whisper model directory."""

    name = PROVIDER_NAME

    def __init__(
        self,
        model_dir: Path | None = None,
        *,
        device: str = "auto",
        compute_type: str = "default",
        beam_size: int = 5,
    ) -> None:
        self._model_dir = Path(model_dir).resolve() if model_dir is not None else None
        self._device = device
        self._compute_type = compute_type
        self._beam_size = beam_size
        self._model: Any | None = None

    @property
    def version(self) -> str:
        return PROVIDER_VERSION

    @property
    def model(self) -> str:
        return self._model_dir.name if self._model_dir is not None else "unconfigured"

    def model_kwargs(self) -> dict[str, Any]:
        """Exact loader arguments. `local_files_only` is not configurable."""
        if self._model_dir is None:
            raise ProviderUnavailableError(
                "faster-whisper model directory is not configured;"
                " downloading a model is not permitted"
            )
        return {
            "model_size_or_path": str(self._model_dir),
            "device": self._device,
            "compute_type": self._compute_type,
            "download_root": str(self._model_dir.parent),
            "local_files_only": True,
        }

    def capability(self) -> ProviderCapability:
        """Report availability without importing weights or touching the network."""
        try:
            _module()
        except ProviderUnavailableError as exc:
            return ProviderCapability(available=False, detail=str(exc))
        if self._model_dir is None:
            return ProviderCapability(
                available=False,
                detail="no local model directory configured; download is not permitted",
            )
        if not self._model_dir.is_dir():
            return ProviderCapability(
                available=False, detail=f"model directory not found: {self._model_dir}"
            )
        missing = missing_model_files(self._model_dir)
        if missing:
            return ProviderCapability(
                available=False,
                detail="local model directory is incomplete, missing {0}".format(
                    ", ".join(missing)
                ),
            )
        return ProviderCapability(
            available=True, detail=f"local model at {self._model_dir}", model=self.model
        )

    def transcribe(
        self, audio_path: Path, language: str, options: dict[str, Any] | None = None
    ) -> TranscriptDTO:
        capability = self.capability()
        if not capability.available:
            raise ProviderUnavailableError(
                f"faster-whisper is unavailable: {capability.detail}"
            )
        audio = Path(audio_path)
        if not audio.is_file():
            raise ProviderUnavailableError(f"STT audio not found: {audio}")

        model = self._load()
        settings = dict(options or {})
        segments, info = model.transcribe(
            str(audio),
            language=language,
            beam_size=int(settings.get("beam_size", self._beam_size)),
            word_timestamps=bool(settings.get("word_timestamps", True)),
            vad_filter=bool(settings.get("vad_filter", True)),
        )
        payload = {
            "language": getattr(info, "language", None) or language,
            "model": self.model,
            "provider_version": self.version,
            "segments": [self._segment(segment) for segment in segments],
        }
        return parse_transcript_dto(payload)

    # -- internals -----------------------------------------------------------

    def _load(self) -> Any:
        if self._model is None:
            whisper = _module()
            kwargs = self.model_kwargs()
            try:
                self._model = whisper.WhisperModel(
                    kwargs.pop("model_size_or_path"), **kwargs
                )
            except Exception as exc:  # pragma: no cover - requires local weights
                raise ProviderUnavailableError(
                    f"faster-whisper could not load the local model: {exc}"
                ) from exc
        return self._model

    @staticmethod
    def _to_ms(value: Any, label: str) -> int:
        """Convert provider seconds into integer milliseconds or fail typed."""
        try:
            seconds = float(value)
        except (TypeError, ValueError) as exc:
            raise ProviderOutputInvalidError(f"{label} is not a number: {value!r}") from exc
        if seconds != seconds or seconds in (float("inf"), float("-inf")):
            raise ProviderOutputInvalidError(f"{label} must be finite, got {value!r}")
        return int(round(seconds * 1000.0))

    @classmethod
    def _segment(cls, segment: Any) -> dict[str, Any]:
        start_ms = cls._to_ms(getattr(segment, "start", None), "segment start")
        end_ms = cls._to_ms(getattr(segment, "end", None), "segment end")
        words = []
        for word in getattr(segment, "words", None) or []:
            words.append(
                {
                    "text": getattr(word, "word", ""),
                    "start_ms": cls._to_ms(getattr(word, "start", None), "word start"),
                    "end_ms": cls._to_ms(getattr(word, "end", None), "word end"),
                }
            )
        return {
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": getattr(segment, "text", ""),
            "confidence": _probability(getattr(segment, "avg_logprob", None)),
            "words": words,
        }


def _probability(avg_logprob: Any) -> float:
    """Map Whisper's average log probability into a `[0, 1]` confidence."""
    if avg_logprob is None:
        return 1.0
    try:
        value = float(avg_logprob)
    except (TypeError, ValueError):
        return 0.0
    if value != value:
        return 0.0
    return max(0.0, min(1.0, math.exp(value)))
