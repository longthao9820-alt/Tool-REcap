from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .voice_system import UnifiedTTSManager, VoiceRecord, VoiceSystemError


LocalSpeechError = VoiceSystemError


@dataclass(frozen=True)
class VoiceChoice:
    key: str
    name: str
    engine: str
    voice_id: str
    languages: tuple[str, ...]
    license_name: str
    source_url: str
    note: str = ""
    gender: str = "neutral"
    styles: tuple[str, ...] = ("neutral",)
    installed: bool = True
    favorite: bool = False
    quality_tier: str = "standard"
    native_language: bool = True
    style_control: bool = False

    @property
    def display_name(self) -> str:
        return self.name


def _choice(record: VoiceRecord, favorites: set[str]) -> VoiceChoice:
    return VoiceChoice(
        key=record.voice_id,
        name=record.label,
        engine=record.engine,
        voice_id=record.voice_id,
        languages=record.supported_languages,
        license_name=record.license,
        source_url=record.source,
        note=record.description,
        gender=record.gender,
        styles=record.styles,
        installed=record.installed,
        favorite=record.voice_id in favorites,
        quality_tier=record.quality_tier,
        native_language=record.native_language,
        style_control=record.style_control,
    )


def _unique_labels(choices: list[VoiceChoice]) -> list[VoiceChoice]:
    counts: dict[str, int] = {}
    for item in choices:
        counts[item.name] = counts.get(item.name, 0) + 1
    return [replace(item, name=f"{item.name} ({item.engine})") if counts[item.name] > 1 else item for item in choices]


def load_voice_catalog() -> list[VoiceChoice]:
    manager = UnifiedTTSManager()
    favorites = manager.catalog.favorites()
    return _unique_labels([_choice(record, favorites) for record in manager.list_voices()])


class LocalSpeechClient:
    """Tương thích project cũ, chuyển mọi yêu cầu sang UnifiedTTSManager."""

    def __init__(self, root: Path | None = None) -> None:
        self.manager = UnifiedTTSManager(root)

    def runtime_status(self) -> dict[str, Any]:
        voices = self.manager.list_voices()
        installed = [item for item in voices if item.installed]
        engines = sorted({item.engine for item in installed})
        return {
            "ready": bool(installed),
            "missing": [] if installed else ["voice_engines"],
            "runtime_root": str(self.manager.runtime),
            "voice_count": len(installed),
            "engine_count": len(engines),
            "engines": engines,
            "preview_count": len(list((self.manager.runtime / "previews").rglob("*.wav"))),
        }

    @staticmethod
    def compatible_voices(language: str) -> list[VoiceChoice]:
        manager = UnifiedTTSManager()
        favorites = manager.catalog.favorites()
        records = manager.list_voices(language, installed_only=True)
        native = [item for item in records if item.native_language and item.language == language]
        return _unique_labels([_choice(item, favorites) for item in (native or records)])

    @staticmethod
    def preview_text(language: str) -> str:
        return UnifiedTTSManager.preview_text(language)

    def preview_path(self, language: str, engine: str, voice_id: str) -> Path:
        del engine
        migrated = self.manager.migrate_voice_id(voice_id)
        return self.manager.catalog.preview_path(migrated, language)

    def synthesize(
        self,
        *,
        text: str,
        output_path: str | Path,
        engine: str,
        voice_id: str,
        language: str,
        style: str = "film_recap",
        timeout: int = 7200,
    ) -> Path:
        del engine, timeout
        return self.manager.synthesize(text=text, output_path=output_path, voice_id=voice_id, language=language, style=style)
