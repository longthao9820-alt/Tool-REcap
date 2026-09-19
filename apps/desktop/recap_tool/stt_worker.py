from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


def _confidence(avg_logprob: Any) -> float:
    try:
        value = float(avg_logprob)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(1.0, math.exp(value)))


def _transcribe(model_dir: Path, audio: Path) -> dict[str, Any]:
    import ctranslate2
    from faster_whisper import WhisperModel

    attempts: list[tuple[str, str]] = []
    try:
        if ctranslate2.get_cuda_device_count() > 0:
            attempts.append(("cuda", "float16"))
    except Exception:
        pass
    attempts.append(("cpu", "int8"))

    failures: list[str] = []
    for device, compute_type in attempts:
        try:
            model = WhisperModel(
                str(model_dir),
                device=device,
                compute_type=compute_type,
                local_files_only=True,
            )
            segments, info = model.transcribe(
                str(audio),
                task="transcribe",
                language=None,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                word_timestamps=False,
                condition_on_previous_text=True,
            )
            rows = []
            for segment in segments:
                text = str(getattr(segment, "text", "")).strip()
                start = max(0.0, float(getattr(segment, "start", 0.0)))
                end = max(start, float(getattr(segment, "end", start)))
                if text and end > start:
                    rows.append(
                        {
                            "start": start,
                            "end": end,
                            "text": text,
                            "confidence": _confidence(getattr(segment, "avg_logprob", None)),
                        }
                    )
            return {
                "language": str(getattr(info, "language", "") or "unknown"),
                "language_probability": float(
                    getattr(info, "language_probability", 0.0) or 0.0
                ),
                "device": device,
                "compute_type": compute_type,
                "segments": rows,
            }
        except Exception as exc:
            failures.append(f"{device}/{compute_type}: {exc}")
    raise RuntimeError("; ".join(failures) or "Không thể khởi tạo Faster-Whisper.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--audio", required=True)
    args = parser.parse_args()
    try:
        result = _transcribe(Path(args.model).resolve(), Path(args.audio).resolve())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
