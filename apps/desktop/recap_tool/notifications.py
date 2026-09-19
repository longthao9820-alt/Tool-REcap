from __future__ import annotations

import ctypes
import os
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from tkinter import messagebox

from .media import open_path


class _FlashWindowInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("hwnd", wintypes.HWND),
        ("dwFlags", wintypes.DWORD),
        ("uCount", wintypes.UINT),
        ("dwTimeout", wintypes.DWORD),
    ]


class DesktopNotifier:
    """Thông báo nổi và nhấp nháy taskbar, không cần dịch vụ ngoài."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._toast: tk.Toplevel | None = None

    def complete(self, completed: int, failed: int, output_root: Path | None = None) -> None:
        if failed:
            title = "Tiến trình đã hoàn tất"
            body = f"Hoàn thành {completed} tập, có {failed} tập bị lỗi."
            accent = "#d97706"
        else:
            title = "Tiến trình đã hoàn tất"
            body = f"Đã render thành công {completed} tập."
            accent = "#168bd8"
        self._show(title, body, accent, output_root)

    def error(self, message: str) -> None:
        self._show("Recap Studio gặp lỗi", message, "#dc2626", None)

    def _show(self, title: str, body: str, accent: str, output_root: Path | None) -> None:
        self.flash_taskbar()
        if os.name == "nt":
            try:
                import winsound

                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:
                pass
        if self._toast and self._toast.winfo_exists():
            self._toast.destroy()
        toast = tk.Toplevel(self.root)
        self._toast = toast
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.configure(bg="#ffffff", highlightbackground=accent, highlightthickness=2)
        frame = tk.Frame(toast, bg="#ffffff", padx=16, pady=12)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text=title, bg="#ffffff", fg="#111827", font=("Segoe UI Semibold", 12), anchor="w").pack(fill="x")
        tk.Label(frame, text=body, bg="#ffffff", fg="#4b5563", font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(5, 8))
        if output_root:
            tk.Button(
                frame,
                text="Mở thư mục kết quả",
                command=lambda: self._open_result(output_root),
                relief="flat",
                bg=accent,
                fg="white",
                activebackground=accent,
                activeforeground="white",
                padx=10,
                pady=4,
            ).pack(anchor="e")
        toast.update_idletasks()
        width = 390
        height = max(112, toast.winfo_reqheight())
        x = max(0, toast.winfo_screenwidth() - width - 18)
        y = max(0, toast.winfo_screenheight() - height - 58)
        toast.geometry(f"{width}x{height}+{x}+{y}")
        toast.after(10000, lambda: toast.destroy() if toast.winfo_exists() else None)

    def _open_result(self, output_root: Path) -> None:
        try:
            open_path(output_root)
        except Exception as exc:
            messagebox.showerror("Không thể mở thư mục kết quả", str(exc), parent=self.root)

    def flash_taskbar(self) -> None:
        if os.name != "nt":
            return
        try:
            info = _FlashWindowInfo(
                ctypes.sizeof(_FlashWindowInfo),
                self.root.winfo_id(),
                0x00000003 | 0x0000000C,
                8,
                0,
            )
            ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
        except Exception:
            pass
