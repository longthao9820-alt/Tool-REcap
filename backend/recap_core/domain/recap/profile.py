"""RecapProfile: the only generation input that may change without re-analysis."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..errors import ValidationError
from ..identity import hash_text
from ..time_range import validate_ms

SCHEMA_VERSION = 1


class RecapStyle(str, Enum):
    FULL_PLOT = "FULL_PLOT"
    FAST_FOCUSED = "FAST_FOCUSED"
    CINEMATIC = "CINEMATIC"
    CHARACTER = "CHARACTER"


class DialoguePolicy(str, Enum):
    NARRATION_ONLY = "NARRATION_ONLY"
    KEY_DIALOGUE = "KEY_DIALOGUE"
    DIALOGUE_HEAVY = "DIALOGUE_HEAVY"


@dataclass(frozen=True)
class RecapProfile:
    """Style, compression and language policy for one recap revision."""

    style: RecapStyle = RecapStyle.FULL_PLOT
    language: str = "vi"
    compression: float = 0.2
    dialogue_policy: DialoguePolicy = DialoguePolicy.KEY_DIALOGUE
    focus_character_id: str | None = None
    max_duration_ms: int | None = None
    speech_rate_wps: float = 2.5

    def __post_init__(self) -> None:
        if not isinstance(self.style, RecapStyle):
            raise ValidationError(f"unknown recap style: {self.style!r}")
        if not isinstance(self.dialogue_policy, DialoguePolicy):
            raise ValidationError(f"unknown dialogue policy: {self.dialogue_policy!r}")
        if not isinstance(self.language, str) or not self.language.strip():
            raise ValidationError("recap language must be non-empty text")
        if not isinstance(self.compression, (int, float)) or isinstance(self.compression, bool):
            raise ValidationError("compression must be a number")
        if not 0.0 < float(self.compression) <= 1.0:
            raise ValidationError("compression must be within (0, 1]")
        if self.max_duration_ms is not None:
            validate_ms(self.max_duration_ms, "max_duration_ms")
            if self.max_duration_ms == 0:
                raise ValidationError("max_duration_ms must be positive when set")
        if (
            not isinstance(self.speech_rate_wps, (int, float))
            or isinstance(self.speech_rate_wps, bool)
            or float(self.speech_rate_wps) <= 0
        ):
            raise ValidationError("speech_rate_wps must be a positive number")
        if self.style is RecapStyle.CHARACTER and not self.focus_character_id:
            raise ValidationError("character style requires focus_character_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "style": self.style.value,
            "language": self.language,
            "compression": float(self.compression),
            "dialogue_policy": self.dialogue_policy.value,
            "focus_character_id": self.focus_character_id,
            "max_duration_ms": self.max_duration_ms,
            "speech_rate_wps": float(self.speech_rate_wps),
        }

    def profile_hash(self) -> str:
        """Stable identity of this profile; drives the recap revision key."""
        payload = self.to_dict()
        return hash_text(*(f"{key}={payload[key]!r}" for key in sorted(payload)))

    @classmethod
    def from_dict(cls, data: object) -> "RecapProfile":
        if not isinstance(data, dict):
            raise ValidationError(f"recap profile must be an object, got {data!r}")
        known = {
            "schema_version",
            "style",
            "language",
            "compression",
            "dialogue_policy",
            "focus_character_id",
            "max_duration_ms",
            "speech_rate_wps",
        }
        unknown = set(data) - known
        if unknown:
            raise ValidationError(f"unknown recap profile fields: {sorted(unknown)}")
        try:
            return cls(
                style=RecapStyle(data.get("style", RecapStyle.FULL_PLOT.value)),
                language=data.get("language", "vi"),
                compression=data.get("compression", 0.2),
                dialogue_policy=DialoguePolicy(
                    data.get("dialogue_policy", DialoguePolicy.KEY_DIALOGUE.value)
                ),
                focus_character_id=data.get("focus_character_id"),
                max_duration_ms=data.get("max_duration_ms"),
                speech_rate_wps=data.get("speech_rate_wps", 2.5),
            )
        except ValueError as exc:
            raise ValidationError(f"malformed recap profile: {exc}") from exc
