from __future__ import annotations

import sys
import tempfile
import time
import traceback
import ctypes
from pathlib import Path
from typing import Any

import tkinter as tk
from PIL import Image


ROOT = Path(__file__).resolve().parents[3]
DESKTOP = ROOT / "apps" / "desktop"
SCREENSHOT = DESKTOP / "smoke-output" / "voice-library-curated.png"
if str(DESKTOP) not in sys.path:
    sys.path.insert(0, str(DESKTOP))

from recap_tool import ui
from recap_tool.audio_preview import AudioPreviewPlayer
from recap_tool.projects import ProjectStore
from recap_tool.settings import SettingsStore
from recap_tool.voice_library import VoiceLibraryDialog
from recap_tool.voice_system import (
    LANGUAGE_NAMES,
    UnifiedTTSManager,
    VoiceCatalog,
    VoiceRecord,
    validate_voice_audio,
)


class IsolatedVoiceCatalog:
    """Use real catalog files while keeping favorite/recent state in memory."""

    def __init__(self, source: VoiceCatalog) -> None:
        self.source = source
        self._favorites: set[str] = set()
        self._recent: list[str] = []

    def load(self) -> list[VoiceRecord]:
        return self.source.rebuild(persist=False)

    def rebuild(self, *, persist: bool = True) -> list[VoiceRecord]:
        del persist
        return self.source.rebuild(persist=False)

    def preview_path(self, voice_id: str, language: str) -> Path:
        return self.source.preview_path(voice_id, language)

    def favorites(self) -> set[str]:
        return set(self._favorites)

    def recent(self) -> list[str]:
        return list(self._recent)

    def set_favorite(self, voice_id: str, favorite: bool) -> None:
        if favorite:
            self._favorites.add(voice_id)
        else:
            self._favorites.discard(voice_id)

    def record_recent(self, voice_id: str) -> None:
        self._recent = [item for item in self._recent if item != voice_id]
        self._recent = [voice_id, *self._recent][:10]


def _isolated_manager(holder: list[UnifiedTTSManager]) -> UnifiedTTSManager:
    manager = UnifiedTTSManager(ROOT)
    manager.catalog = IsolatedVoiceCatalog(manager.catalog)  # type: ignore[assignment]
    holder.append(manager)
    return manager


def _dialog(root: tk.Tk) -> VoiceLibraryDialog | None:
    return next(
        (
            child
            for child in root.winfo_children()
            if isinstance(child, VoiceLibraryDialog)
        ),
        None,
    )


def _capture_dialog(dialog: VoiceLibraryDialog) -> None:
    dialog.deiconify()
    dialog.lift()
    dialog.focus_force()
    dialog.update_idletasks()
    x = dialog.winfo_rootx()
    y = dialog.winfo_rooty()
    width = dialog.winfo_width()
    height = dialog.winfo_height()
    if width <= 0 or height <= 0:
        raise RuntimeError("VoiceLibraryDialog không có kích thước hiển thị.")
    SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
    # Capture the window itself, not another app covering the desktop.
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    from ctypes import wintypes

    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = wintypes.HANDLE
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi32.GetBitmapBits.argtypes = [wintypes.HBITMAP, wintypes.LONG, ctypes.c_void_p]
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    hwnd = dialog.winfo_id()
    dc = user32.GetDC(hwnd)
    memory = gdi32.CreateCompatibleDC(dc)
    bitmap = gdi32.CreateCompatibleBitmap(dc, width, height)
    previous = gdi32.SelectObject(memory, bitmap)
    try:
        if not user32.PrintWindow(hwnd, memory, 1):
            raise RuntimeError("Không chụp được cửa sổ giọng đọc.")
        buffer = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetBitmapBits(bitmap, len(buffer), buffer)
        image = Image.frombytes("RGB", (width, height), buffer.raw, "raw", "BGRX", 0, 1)
    finally:
        gdi32.SelectObject(memory, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(hwnd, dc)
    image.save(SCREENSHOT)
    if not SCREENSHOT.is_file() or SCREENSHOT.stat().st_size == 0:
        raise RuntimeError(f"Không tạo được screenshot: {SCREENSHOT}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    root: ui.RecapStudioApp | None = None
    manager_holder: list[UnifiedTTSManager] = []
    temp_dir = tempfile.TemporaryDirectory(prefix="qa_voice_ui_")
    temp_root = Path(temp_dir.name)
    original_project_store = ui.ProjectStore
    original_settings_store = ui.SettingsStore
    original_manager = ui.UnifiedTTSManager
    original_gpu_async = ui.RecapStudioApp._detect_gpu_async
    result: dict[str, Any] = {}

    try:
        ui.ProjectStore = lambda: ProjectStore(temp_root / "projects.json")  # type: ignore[assignment]
        ui.SettingsStore = lambda: SettingsStore(temp_root / "settings.json")  # type: ignore[assignment]
        ui.UnifiedTTSManager = lambda: _isolated_manager(manager_holder)  # type: ignore[assignment]
        ui.RecapStudioApp._detect_gpu_async = lambda _self: None

        root = ui.RecapStudioApp()
        root.state("normal")
        root.geometry("1280x800+0+0")
        root.update_idletasks()
        root.lift()

        def fail(exc: BaseException) -> None:
            result["error"] = exc
            dialog = _dialog(root)
            if dialog is not None:
                dialog.destroy()

        def wait_for_preview(dialog: VoiceLibraryDialog) -> None:
            try:
                expected = result["preview_path"]
                if AudioPreviewPlayer.current() == expected:
                    AudioPreviewPlayer.stop()
                    if AudioPreviewPlayer.current() is not None:
                        raise RuntimeError("AudioPreviewPlayer.stop không dừng preview.")
                    _capture_dialog(dialog)
                    dialog._choose()
                    return
                if time.monotonic() >= result["preview_deadline"]:
                    raise RuntimeError("Không nhận được preview qua AudioPreviewPlayer.play.")
                root.after(50, lambda: wait_for_preview(dialog))
            except BaseException as exc:
                fail(exc)

        def exercise_dialog(dialog: VoiceLibraryDialog) -> None:
            manager = manager_holder[0]
            voices = manager.catalog.load()
            if not voices:
                raise RuntimeError("Catalog không có voice để kiểm tra.")

            counts: dict[str, int] = {}
            for language, language_name in LANGUAGE_NAMES.items():
                dialog.language_var.set(language_name)
                dialog.search_var.set("")
                dialog._refresh()
                visible = [
                    dialog._voices[item]
                    for item in dialog.tree.get_children()
                    if item in dialog._voices
                ]
                if not visible:
                    raise RuntimeError(f"Không có voice hiển thị cho {language}.")
                if any(item.gender == "neutral" for item in visible):
                    raise RuntimeError(f"Catalog hiển thị voice neutral cho {language}.")
                if any(language not in item.supported_languages for item in visible):
                    raise RuntimeError(f"Bộ lọc language lỗi cho {language}.")
                counts[language] = len(visible)

            dialog.search_var.set("__qa_voice_ui_no_such_voice__")
            dialog._refresh()
            if dialog.tree.get_children():
                raise RuntimeError("Tìm kiếm không tồn tại vẫn trả về voice.")

            dialog.search_var.set("")
            candidates = [
                item
                for item in voices
                if item.installed
                and item.gender in {"male", "female"}
                and item.preview_available
                and manager.catalog.preview_path(item.voice_id, item.language).is_file()
            ]
            candidates.sort(key=lambda item: (item.language != "de-DE", item.display_name.casefold()))
            selected: VoiceRecord | None = None
            preview_path: Path | None = None
            for candidate in candidates:
                candidate_path = manager.catalog.preview_path(candidate.voice_id, candidate.language)
                try:
                    validate_voice_audio(candidate_path)
                except Exception:
                    continue
                selected = candidate
                preview_path = candidate_path.resolve()
                break
            if selected is None or preview_path is None:
                raise RuntimeError("Không có preview WAV hợp lệ để kiểm tra.")

            dialog.language_var.set(LANGUAGE_NAMES[selected.language])
            dialog._refresh()
            if not dialog.tree.exists(selected.voice_id):
                raise RuntimeError(f"Không chọn được voice {selected.voice_id}.")
            dialog.tree.selection_set(selected.voice_id)
            dialog.tree.focus(selected.voice_id)
            dialog.tree.see(selected.voice_id)
            if dialog._selected() is None or dialog._selected().gender not in {"male", "female"}:
                raise RuntimeError("Voice được chọn không hợp lệ hoặc là neutral.")

            result["language_counts"] = counts
            result["selected_voice"] = selected.voice_id
            result["preview_path"] = preview_path
            result["preview_deadline"] = time.monotonic() + 10
            dialog._preview()
            root.after(50, lambda: wait_for_preview(dialog))

        def open_library() -> None:
            try:
                root._open_voice_library()
            except BaseException as exc:
                result["error"] = exc
            finally:
                root.after_idle(root.quit)

        def find_dialog() -> None:
            try:
                dialog = _dialog(root)
                if dialog is None:
                    if time.monotonic() >= result["dialog_deadline"]:
                        raise RuntimeError("Không mở được VoiceLibraryDialog.")
                    root.after(50, find_dialog)
                    return
                dialog.geometry("1180x720+50+40")
                dialog.update_idletasks()
                dialog.lift()
                dialog.focus_force()
                exercise_dialog(dialog)
            except BaseException as exc:
                fail(exc)

        result["dialog_deadline"] = time.monotonic() + 10
        root.after(100, open_library)
        root.after(150, find_dialog)
        root.mainloop()

        if "error" in result:
            raise result["error"]
        if not SCREENSHOT.is_file():
            raise RuntimeError(f"Thiếu screenshot: {SCREENSHOT}")
        print("PASS: VoiceLibraryDialog filter, selection, no-result search, preview, stop")
        print(f"Language counts: {result['language_counts']}")
        print(f"Selected voice: {result['selected_voice']}")
        print(f"Screenshot: {SCREENSHOT}")
        return 0
    except BaseException as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        traceback.print_exc()
        if SCREENSHOT.is_file():
            print(f"Screenshot: {SCREENSHOT}", file=sys.stderr)
        return 1
    finally:
        AudioPreviewPlayer.stop()
        for manager in manager_holder:
            manager.shutdown_all()
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass
        ui.ProjectStore = original_project_store
        ui.SettingsStore = original_settings_store
        ui.UnifiedTTSManager = original_manager
        ui.RecapStudioApp._detect_gpu_async = original_gpu_async
        temp_dir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
