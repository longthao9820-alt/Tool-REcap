from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .credentials import default_data_directory


@dataclass
class AppSettings:
    max_concurrent_projects: int = 1
    generate_srt: bool = True
    burn_subtitles: bool = True
    quality: str = "high"
    video_codec: str = "h264"
    use_gpu: bool = True
    notify_complete: bool = True
    notify_error: bool = True
    output_subdirectory: str = "recaps_da_render"
    last_json_directory: str = ""
    last_media_directory: str = ""
    api_endpoint: str = "http://127.0.0.1:20128/v1"
    api_key: str = ""
    scanner_model: str = "sub"
    scanner_thinking: str = "max"
    finalizer_model: str = "prime"
    finalizer_thinking: str = "high"
    scanner_parallelism: int = 2
    # Legacy single-model fields are kept so the original RecapStudio UI and
    # existing settings files remain readable.  The automatic edition uses
    # the explicit Scanner/Finalizer fields above.
    api_model: str = "gpt-5.6-sol-dual"
    api_thinking: str = "high"
    api_parallelism: int = 2
    api_chunk_seconds: int = 300
    recap_language: str = "en-US"
    recap_mode: str = "MAIN_STORIES"
    content_type: str = "US_TV_SHOW"
    source_rights_status: str = "UNVERIFIED"
    voice_id: str = ""
    voice_style: str = "film_recap"
    recap_prompt: str = ""
    recap_prompt_version: str = ""


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_data_directory() / "settings.json"

    def load(self) -> AppSettings:
        if not self.path.is_file():
            return AppSettings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            allowed = AppSettings.__dataclass_fields__
            values = {key: value for key, value in raw.items() if key in allowed}
            return AppSettings(**values)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return AppSettings()

    def save(self, settings: AppSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".partial")
        temporary.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)
