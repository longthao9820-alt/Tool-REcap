from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

from recap_tool.ui import run_app


def _voice_self_test(result_path: Path) -> int:
    from recap_tool.media import probe_duration
    from recap_tool.tts import LocalSpeechClient

    result_path.parent.mkdir(parents=True, exist_ok=True)
    english = result_path.with_name("voice-self-test-en.wav")
    payload: dict[str, object]
    try:
        client = LocalSpeechClient()
        english_voice = client.manager.list_voices("en-US", installed_only=True)[0]
        client.synthesize(
            text="This is the selected English narration voice.",
            output_path=english,
            engine=english_voice.engine,
            voice_id=english_voice.voice_id,
            language="en-US",
        )
        payload = {
            "success": True,
            "english_voice_id": english_voice.voice_id,
            "english_seconds": round(probe_duration(english), 3),
            "english_file": str(english),
            "voice_runtime": "local",
        }
    except Exception as exc:
        payload = {"success": False, "error": str(exc), "traceback": traceback.format_exc()}
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if payload["success"] else 1


def _report_startup_error(exc: BaseException) -> None:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    log = base / "RecapStudio" / "startup-error.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(traceback.format_exc(), encoding="utf-8")
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Recap Studio không thể khởi động",
            f"{exc}\n\nChi tiết đã được lưu tại:\n{log}",
            parent=root,
        )
        root.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--voice-self-test":
        raise SystemExit(_voice_self_test(Path(sys.argv[2]).resolve()))
    try:
        run_app()
    except BaseException as exc:
        _report_startup_error(exc)
        raise
