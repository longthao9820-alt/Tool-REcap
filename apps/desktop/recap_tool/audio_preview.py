from __future__ import annotations

import os
import threading
from pathlib import Path


class AudioPreviewError(RuntimeError):
    pass


class AudioPreviewPlayer:
    """Phát WAV ngay trong tiến trình Recap Studio, không mở ứng dụng ngoài."""

    _lock = threading.RLock()
    _current: Path | None = None

    @classmethod
    def play(cls, path: str | Path) -> Path:
        audio = Path(path).expanduser().resolve()
        if not audio.is_file():
            raise AudioPreviewError(f"Không tìm thấy tệp nghe thử: {audio}")
        if audio.suffix.lower() != ".wav":
            raise AudioPreviewError("Trình nghe thử nội bộ hiện hỗ trợ tệp WAV.")
        if os.name != "nt":
            raise AudioPreviewError("Trình nghe thử nội bộ hiện chỉ hỗ trợ Windows.")
        import winsound

        with cls._lock:
            winsound.PlaySound(None, winsound.SND_PURGE)
            winsound.PlaySound(
                str(audio),
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
            cls._current = audio
        return audio

    @classmethod
    def stop(cls) -> None:
        if os.name != "nt":
            return
        import winsound

        with cls._lock:
            winsound.PlaySound(None, winsound.SND_PURGE)
            cls._current = None

    @classmethod
    def current(cls) -> Path | None:
        with cls._lock:
            return cls._current

