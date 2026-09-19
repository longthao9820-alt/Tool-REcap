from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path


def _configure_isolated_data() -> Path:
    configured = os.environ.get("BODYCAM_STUDIO_DATA")
    if configured:
        data_directory = Path(configured).expanduser().resolve()
    else:
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        data_directory = local / "BodycamStudio"
    os.environ["RECAP_STUDIO_DATA"] = str(data_directory)
    return data_directory


DATA_DIRECTORY = _configure_isolated_data()

from recap_tool.ui import run_app  # noqa: E402


def _ensure_bodycam_defaults() -> None:
    from recap_tool.settings import SettingsStore

    store = SettingsStore()
    settings = store.load()
    if settings.output_subdirectory == "recaps_da_render":
        settings.output_subdirectory = "bodycam_da_render"
        store.save(settings)


def _self_test(result_path: Path) -> int:
    from recap_tool.recap_api import _schema, recap_prompt_for_content
    from recap_tool.settings import SettingsStore

    result_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        prompt = recap_prompt_for_content("BODYCAM")
        schema = _schema()
        settings = SettingsStore().load()
        payload = {
            "success": (
                "BODYCAM EVIDENCE-LED COMMENTARY" in prompt
                and "BODYCAM" in schema["properties"]["content_type"]["enum"]
            ),
            "data_directory": str(DATA_DIRECTORY),
            "default_output_subdirectory": settings.output_subdirectory,
            "prompt_characters": len(prompt),
            "content_type": "BODYCAM",
        }
    except Exception as exc:
        payload = {"success": False, "error": str(exc), "traceback": traceback.format_exc()}
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if payload["success"] else 1


def _report_startup_error(exc: BaseException) -> None:
    log = DATA_DIRECTORY / "startup-error.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(traceback.format_exc(), encoding="utf-8")
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Bodycam Studio không thể khởi động",
            f"{exc}\n\nChi tiết đã được lưu tại:\n{log}",
            parent=root,
        )
        root.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    _ensure_bodycam_defaults()
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        raise SystemExit(_self_test(Path(sys.argv[2]).resolve()))
    try:
        run_app(
            app_title="Bodycam Studio — Evidence-led Commentary",
            locked_content_type="BODYCAM",
        )
    except BaseException as exc:
        _report_startup_error(exc)
        raise
