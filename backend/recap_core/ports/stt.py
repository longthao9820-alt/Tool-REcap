"""STT port and its provider-neutral DTO.

Providers return plain data. Validation happens here, at the boundary, so a
malformed or non-finite provider result can never reach the domain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..domain.errors import ProviderOutputInvalidError, ValidationError
from ..domain.time_range import validate_confidence


@dataclass(frozen=True)
class ProviderCapability:
    """Result of a health probe. `available` is never assumed."""

    available: bool
    detail: str
    model: str | None = None


@dataclass(frozen=True)
class TranscriptSegmentDTO:
    start_ms: int
    end_ms: int
    text: str
    speaker_id: str | None = None
    confidence: float = 1.0
    words: tuple[dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TranscriptDTO:
    language: str
    segments: tuple[TranscriptSegmentDTO, ...]
    model: str
    provider_version: str


def _require_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProviderOutputInvalidError(
            f"{field_name} must be an integer millisecond value, got {value!r}"
        )
    if value < 0:
        raise ProviderOutputInvalidError(f"{field_name} must not be negative, got {value!r}")
    return value


def parse_transcript_dto(payload: object) -> TranscriptDTO:
    """Validate raw provider output into a TranscriptDTO or fail typed."""
    if not isinstance(payload, dict):
        raise ProviderOutputInvalidError(f"transcript payload must be an object, got {payload!r}")
    for key in ("language", "segments", "model", "provider_version"):
        if key not in payload:
            raise ProviderOutputInvalidError(f"transcript payload is missing {key!r}")
    if not isinstance(payload["language"], str) or not payload["language"].strip():
        raise ProviderOutputInvalidError("transcript language must be non-empty text")
    raw_segments = payload["segments"]
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ProviderOutputInvalidError("transcript must contain at least one segment")

    segments: list[TranscriptSegmentDTO] = []
    for index, raw in enumerate(raw_segments):
        if not isinstance(raw, dict):
            raise ProviderOutputInvalidError(f"transcript segment {index} is not an object")
        start_ms = _require_int(raw.get("start_ms"), f"segment {index} start_ms")
        end_ms = _require_int(raw.get("end_ms"), f"segment {index} end_ms")
        if end_ms <= start_ms:
            raise ProviderOutputInvalidError(
                f"segment {index} end_ms must be greater than start_ms"
            )
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ProviderOutputInvalidError(f"segment {index} text must be non-empty")
        try:
            confidence = validate_confidence(raw.get("confidence", 1.0))
        except ValidationError as exc:
            raise ProviderOutputInvalidError(f"segment {index}: {exc}") from exc
        words = raw.get("words") or []
        if not isinstance(words, list):
            raise ProviderOutputInvalidError(f"segment {index} words must be a list")
        parsed_words: list[dict[str, Any]] = []
        for word_index, word in enumerate(words):
            if not isinstance(word, dict):
                raise ProviderOutputInvalidError(
                    f"segment {index} word {word_index} is not an object"
                )
            word_start = _require_int(
                word.get("start_ms"), f"segment {index} word {word_index} start_ms"
            )
            word_end = _require_int(
                word.get("end_ms"), f"segment {index} word {word_index} end_ms"
            )
            word_text = word.get("text")
            if not isinstance(word_text, str) or not word_text.strip():
                raise ProviderOutputInvalidError(
                    f"segment {index} word {word_index} text must be non-empty"
                )
            if not (start_ms <= word_start < word_end <= end_ms):
                raise ProviderOutputInvalidError(
                    f"segment {index} word {word_index} is outside its segment"
                )
            parsed_words.append(
                {"text": word_text, "start_ms": word_start, "end_ms": word_end}
            )
        speaker_id = raw.get("speaker_id")
        if speaker_id is not None and not isinstance(speaker_id, str):
            raise ProviderOutputInvalidError(f"segment {index} speaker_id must be text or null")
        segments.append(
            TranscriptSegmentDTO(
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
                speaker_id=speaker_id,
                confidence=confidence,
                words=tuple(parsed_words),
            )
        )

    ordered = sorted(segments, key=lambda segment: segment.start_ms)
    previous_end = -1
    for segment in ordered:
        if segment.start_ms < previous_end:
            raise ProviderOutputInvalidError("transcript segments overlap")
        previous_end = segment.end_ms

    for key in ("model", "provider_version"):
        if not isinstance(payload[key], str) or not payload[key].strip():
            raise ProviderOutputInvalidError(f"transcript {key} must be non-empty text")
    return TranscriptDTO(
        language=payload["language"],
        segments=tuple(ordered),
        model=payload["model"],
        provider_version=payload["provider_version"],
    )


def ensure_transcript_dto(value: object) -> TranscriptDTO:
    """Re-validate a provider result at the boundary, DTO instance or raw payload.

    An adapter that already returns a `TranscriptDTO` still passes through the same
    checks, so orchestration never has to trust an adapter's own discipline.
    """
    if isinstance(value, TranscriptDTO):
        payload = {
            "language": value.language,
            "model": value.model,
            "provider_version": value.provider_version,
            "segments": [
                {
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "text": segment.text,
                    "speaker_id": segment.speaker_id,
                    "confidence": segment.confidence,
                    "words": [dict(word) for word in segment.words],
                }
                for segment in value.segments
            ],
        }
        return parse_transcript_dto(payload)
    return parse_transcript_dto(value)


class STTProvider(Protocol):
    """`transcribe(audio, language, options) -> TranscriptDTO`."""

    name: str

    @property
    def version(self) -> str: ...

    @property
    def model(self) -> str: ...

    def capability(self) -> ProviderCapability: ...

    def transcribe(
        self, audio_path: Path, language: str, options: dict[str, Any] | None = None
    ) -> TranscriptDTO: ...
