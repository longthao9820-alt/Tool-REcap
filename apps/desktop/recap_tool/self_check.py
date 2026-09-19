from __future__ import annotations

import sys

from .gpu import bundled_binary, detect_gpu_encoder
from .speech_to_text import speech_to_text_status
from .tts import LocalSpeechClient
from .voice_system import UnifiedTTSManager


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("KIỂM TRA CÔNG CỤ VIDEO RECAP")
    print("=" * 38)
    ok = True
    print(f"Python: {sys.version.split()[0]}")
    for name in ("ffmpeg", "ffprobe"):
        path = bundled_binary(name)
        if path:
            print(f"✓ {name}: {path}")
        else:
            print(f"✗ Không tìm thấy {name}")
            ok = False
    try:
        info = LocalSpeechClient().runtime_status()
        if not info["ready"]:
            raise RuntimeError("Thiếu: " + ", ".join(info["missing"]))
        print(f"✓ Runtime giọng cục bộ: {info['voice_count']} giọng")
        manager = UnifiedTTSManager()
        for engine in info["engines"]:
            health = manager.health_check(engine)
            mark = "✓" if health.ready else "✗"
            print(f"{mark} {engine}: {health.status}")
            ok = ok and health.ready
    except Exception as exc:
        print(f"✗ Runtime giọng cục bộ: {exc}")
        ok = False
    stt_ready, stt_detail = speech_to_text_status()
    print(f"{'✓' if stt_ready else '✗'} Nhận dạng lời thoại: {stt_detail}")
    ok = ok and stt_ready
    gpu = detect_gpu_encoder(refresh=True)
    if gpu.available:
        print(f"✓ GPU: {gpu.gpu_name} / {gpu.encoder}")
    else:
        print(f"✗ GPU: {gpu.reason}")
        ok = False
    print("=" * 38)
    print("Hệ thống đã sẵn sàng." if ok else "Hệ thống chưa sẵn sàng. Hãy sửa các mục có dấu ✗.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
