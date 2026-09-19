from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DESKTOP = ROOT / "apps" / "desktop"
if str(DESKTOP) not in sys.path:
    sys.path.insert(0, str(DESKTOP))

from recap_tool.voice_system import UnifiedTTSManager


def main() -> int:
    manager = UnifiedTTSManager(ROOT)
    failed = []
    for voice in manager.list_voices(installed_only=True):
        languages = (voice.language,)
        for language in languages:
            output = manager.catalog.preview_path(voice.voice_id, language)
            if output.is_file() and output.stat().st_size > 128:
                print(f"SKIP {voice.voice_id} {language}")
                continue
            try:
                manager.synthesize(text=manager.preview_text(language), output_path=output, voice_id=voice.voice_id, language=language, style="film_recap")
                print(f"OK {voice.voice_id} {language}")
            except Exception as exc:
                failed.append(f"{voice.voice_id}/{language}: {exc}")
                print(f"FAILED {failed[-1]}")
    manager.catalog.rebuild()
    manager.shutdown_all()
    if failed:
        print("\n".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
