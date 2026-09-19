from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

from recap_tool.auto_ui import run_auto_app


def _report_startup_error(exc: BaseException) -> None:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    log = base / "RecapStudio" / "auto-startup-error.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(traceback.format_exc(), encoding="utf-8")
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Toolrecap không thể khởi động",
            f"{exc}\n\nChi tiết đã được lưu tại:\n{log}",
            parent=root,
        )
        root.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        run_auto_app()
    except BaseException as exc:
        _report_startup_error(exc)
        raise

