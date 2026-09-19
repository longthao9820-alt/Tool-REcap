from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[3]
DESKTOP = ROOT / "apps" / "desktop"
if str(DESKTOP) not in sys.path:
    sys.path.insert(0, str(DESKTOP))

from recap_tool.voice_system import VoiceCatalog


def main() -> int:
    catalog = VoiceCatalog(ROOT)
    rows = []
    failed = []
    for voice in catalog.rebuild():
        if not voice.installed:
            continue
        path = catalog.preview_path(voice.voice_id, voice.language)
        try:
            audio, rate = sf.read(path, dtype="float32")
            values = np.asarray(audio, dtype=np.float32).reshape(-1)
            duration = len(audio) / max(rate, 1)
            peak = float(np.max(np.abs(values))) if len(values) else 0.0
            rms = float(np.sqrt(np.mean(values * values))) if len(values) else 0.0
            silence_ratio = float(np.mean(np.abs(values) < 0.0005)) if len(values) else 1.0
            issues = []
            if duration < 1.0 or duration > 25.0:
                issues.append("duration")
            if peak <= 0.01 or peak >= 1.0:
                issues.append("level")
            if rms <= 0.003:
                issues.append("silence")
            if not np.isfinite(values).all():
                issues.append("non_finite")
            rows.append({"voice_id": voice.voice_id, "engine": voice.engine, "language": voice.language, "duration": round(duration, 3), "peak": round(peak, 5), "rms": round(rms, 5), "silence_ratio": round(silence_ratio, 4), "issues": issues})
            if issues:
                failed.append(f"{voice.voice_id}: {','.join(issues)}")
        except Exception as exc:
            failed.append(f"{voice.voice_id}: {exc}")
    report = ROOT / "runtime" / "speech" / "logs" / "voice-preview-qa.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"voices_checked": len(rows), "failed": failed, "results": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Checked={len(rows)} Failed={len(failed)} Report={report}")
    if failed:
        print("\n".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
