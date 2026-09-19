from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DESKTOP = ROOT / "apps" / "desktop"
if str(DESKTOP) not in sys.path:
    sys.path.insert(0, str(DESKTOP))

from recap_tool.media import find_binary, probe_duration, probe_media
from recap_tool.renderer import RecapRenderer, RenderSettings
from recap_tool.voice_system import UnifiedTTSManager


CASES = [
    ("voicestudio-us", "voicestudio.en.documentarian", "en-US", "The missing evidence changes who controls the investigation."),
    ("voicestudio-gb", "voicestudio.en.commentator", "en-GB", "The missing evidence changes who controls the investigation."),
]


def main() -> int:
    work = DESKTOP / "smoke-output" / "curated-render"
    work.mkdir(parents=True, exist_ok=True)
    manager = UnifiedTTSManager(ROOT)
    for engine, voice_id, language, text in CASES:
        if not voice_id:
            voice_id = manager.list_voices(language, installed_only=True)[0].voice_id
        probe = manager.synthesize(text=text, output_path=work / f"{engine}-probe.wav", voice_id=voice_id, language=language, style="film_recap")
        duration = probe_duration(probe)
        source = work / f"{engine}-source.mp4"
        subprocess.run([find_binary("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=25:duration={duration + 0.8:.3f}", "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration + 0.8:.3f}", "-c:v", "h264_nvenc", "-preset", "p4", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)], check=True)
        payload = {
            "schema_version": "1.0",
            "project_id": f"multi-{engine}",
            "source_video": str(source),
            "classification": {"content_type": "US_TV_SHOW", "source_language": language, "recap_language": language, "confidence": 1.0, "reason": "VoiceStudio production test."},
            "source_rights": {"status": "OWNED", "notes": "Synthetic smoke-test footage."},
            "render_policy": {"aspect_ratio_policy": "preserve_source", "voice_speed": 1.0, "video_speed_absolute_min": 0.85, "video_speed_absolute_max": 1.15},
            "outputs": [{"render_id": "recap", "type": "FULL_RECAP", "title": "Test", "segments": [{"segment_id": f"{engine}-segment", "order": 1, "segment_type": "narration", "purpose": "CAUSAL_ANALYSIS", "editorial_value": "Explains the consequence supported by the selected evidence.", "narration_text": text, "estimated_voice_duration_ms": round(duration * 1000), "source_clips": [{"clip_id": f"{engine}-clip", "source_video": str(source), "start_ms": 0, "end_ms": round(duration * 1000), "order": 1}], "original_audio": "mute", "preserve_original_audio": False, "subtitle": True}] }],
        }
        manifest = work / f"{engine}.json"
        manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        results = RecapRenderer(log=print).render(RenderSettings(manifest_path=manifest, source_override=source, output_directory=work / engine, voice_engine=engine, voice_id=voice_id, voice_style="film_recap", generate_srt=True, burn_subtitles=True, quality="standard", use_gpu=True))
        info = probe_media(results[0].video_path)
        if (info["width"], info["height"], info["has_audio"]) != (640, 360, True):
            raise RuntimeError(f"Output validation failed for {engine}: {info}")
        report = json.loads(((work / engine) / f"multi-{engine}-bao-cao-render.json").read_text(encoding="utf-8"))
        if report["video_encoder"] != "h264_nvenc":
            raise RuntimeError(f"GPU encoder not used for {engine}")
        print(f"PASS {engine}: {results[0].video_path}")
    manager.shutdown_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
