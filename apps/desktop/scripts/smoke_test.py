from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recap_tool.media import find_binary, probe_duration, probe_media
from recap_tool.renderer import RecapRenderer, RenderSettings
from recap_tool.tts import LocalSpeechClient


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    work = ROOT / "smoke-output"
    work.mkdir(parents=True, exist_ok=True)
    voice_probe = work / "voice-probe.wav"
    text = "The apparent victory quietly creates the episode's next conflict."
    client = LocalSpeechClient()
    voice = client.manager.list_voices("en-US", installed_only=True)[0]
    print(f"Tạo giọng thử cục bộ bằng {voice.voice_id}…")
    client.synthesize(
        text=text,
        output_path=voice_probe,
        engine=voice.engine,
        voice_id=voice.voice_id,
        language="en-US",
    )
    voice_duration = probe_duration(voice_probe)
    clip_duration = max(1.0, voice_duration)

    source = work / "video-nguon-thu.mp4"
    print("Tạo video nguồn tổng hợp 640 × 360…")
    command = [
        find_binary("ffmpeg"), "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=25:duration={clip_duration + 1.0:.3f}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={clip_duration + 1.0:.3f}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(source),
    ]
    subprocess.run(command, check=True, capture_output=True)

    manifest = {
        "schema_version": "1.0",
        "project_id": "kiem-tra-dau-cuoi",
        "source_video": str(source),
        "classification": {
            "content_type": "US_TV_SHOW",
            "source_language": "en-US",
            "recap_language": "en-US",
            "confidence": 1.0,
            "reason": "Bản kiểm tra kỹ thuật.",
        },
        "render_policy": {
            "aspect_ratio_policy": "preserve_source",
            "voice_speed": 1.0,
            "video_speed_min": 0.9,
            "video_speed_max": 1.1,
            "video_speed_absolute_min": 0.85,
            "video_speed_absolute_max": 1.15,
        },
        "outputs": [
            {
                "render_id": "highlight-thu",
                "type": "HIGHLIGHT",
                "title": "Kiểm tra",
                "segments": [
                    {
                        "segment_id": "phan-001",
                        "order": 1,
                        "segment_type": "narration",
                        "purpose": "CAUSAL_ANALYSIS",
                        "editorial_value": "Explains the verified consequence rather than repeating the visible action.",
                        "narration_text": text,
                        "estimated_voice_duration_ms": round(voice_duration * 1000),
                        "source_clips": [
                            {
                                "clip_id": "clip-001",
                                "source_video": str(source),
                                "start_ms": 0,
                                "end_ms": round(clip_duration * 1000),
                                "order": 1,
                            }
                        ],
                        "original_audio": "mute",
                        "preserve_original_audio": False,
                        "subtitle": True,
                    }
                ],
            }
        ],
        "source_rights": {"status": "OWNED", "notes": "Synthetic smoke-test footage."},
    }
    manifest_path = work / "recap_project.smoke.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Render video thử có giọng và phụ đề…")
    renderer = RecapRenderer(
        progress=lambda current, total, message: print(f"[{current}/{total}] {message}"),
        log=print,
    )
    results = renderer.render(
        RenderSettings(
            manifest_path=manifest_path,
            source_override=source,
            output_directory=work,
            voice_engine=voice.engine,
            voice_id=voice.voice_id,
            generate_srt=True,
            burn_subtitles=True,
            quality="standard",
            use_gpu=True,
        )
    )
    result = results[0]
    info = probe_media(result.video_path)
    if (info["width"], info["height"]) != (640, 360):
        raise RuntimeError("Video đầu ra không giữ đúng tỷ lệ nguồn.")
    print(f"THÀNH CÔNG: {result.video_path}")
    print(f"Thời lượng: {result.duration_seconds:.2f}s; tỷ lệ: {info['aspect_ratio']}")
    voice_probe.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
