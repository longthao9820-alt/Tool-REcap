from __future__ import annotations

import json
import re
import sys
import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DESKTOP = ROOT / "apps" / "desktop"
if str(DESKTOP) not in sys.path:
    sys.path.insert(0, str(DESKTOP))

from recap_tool.voice_system import UnifiedTTSManager


def words(value: str) -> list[str]:
    return re.findall(r"[^\W\d_]+", value.casefold())


def word_error_rate(reference: str, hypothesis: str) -> float:
    expected = words(reference)
    actual = words(hypothesis)
    previous = list(range(len(actual) + 1))
    for row, source in enumerate(expected, start=1):
        current = [row]
        for column, target in enumerate(actual, start=1):
            current.append(min(current[-1] + 1, previous[column] + 1, previous[column - 1] + (source != target)))
        previous = current
    return previous[-1] / max(len(expected), 1)


def main() -> int:
    from faster_whisper import WhisperModel

    parser = argparse.ArgumentParser()
    parser.add_argument("--languages", nargs="+", default=["de-DE"])
    args = parser.parse_args()
    manager = UnifiedTTSManager(ROOT)
    model_root = ROOT / "runtime" / "speech" / "models" / "qa-whisper"
    try:
        model = WhisperModel("small", device="cuda", compute_type="float16", download_root=str(model_root))
    except Exception:
        model = WhisperModel("small", device="cpu", compute_type="int8", download_root=str(model_root))

    results = []
    for voice in manager.list_voices(installed_only=True):
        if voice.language not in args.languages:
            continue
        expected = manager.preview_text(voice.language)
        preview = manager.catalog.preview_path(voice.voice_id, voice.language)
        if not preview.is_file():
            raise RuntimeError(f"Missing preview: {voice.voice_id}")
        segments, info = model.transcribe(
            str(preview),
            language=voice.language.split("-", 1)[0],
            beam_size=5,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        resolved = list(segments)
        transcript = " ".join(item.text.strip() for item in resolved).strip()
        wer = word_error_rate(expected, transcript)
        row = {
            "voice_id": voice.voice_id,
            "engine": voice.engine,
            "language": voice.language,
            "transcript": transcript,
            "wer": round(wer, 4),
            "word_count": len(words(transcript)),
            "avg_logprob": round(sum(item.avg_logprob for item in resolved) / max(len(resolved), 1), 4),
            "language_probability": round(info.language_probability, 4),
        }
        results.append(row)
        print(f"{voice.voice_id}: WER={wer:.2f} | {transcript}", flush=True)

    results.sort(key=lambda item: (item["wer"], -item["avg_logprob"], item["voice_id"]))
    report = ROOT / "runtime" / "speech" / "logs" / "curated-intelligibility-qa.json"
    failed = [item["voice_id"] for item in results if item["wer"] > 0.2]
    if not results:
        raise RuntimeError("No voices checked")
    report.write_text(json.dumps({"references": {lang: manager.preview_text(lang) for lang in args.languages}, "results": results, "failed": failed}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report={report}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
