from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from .api_client import OpenAICompatibleClient
from .audio_preview import AudioPreviewPlayer
from .recap_api import recap_prompt_for_content
from .settings import AppSettings, SettingsStore
from .tts import LocalSpeechClient, VoiceChoice
from .voice_system import STYLE_NAMES, UnifiedTTSManager


LANGUAGE_LABELS = {"en-US": "English (US)", "en-GB": "English (UK)"}
MODE_LABELS = {"MAIN_STORIES": "Nội dung chính", "FULL_EPISODE": "Recap cả tập"}
CONTENT_LABELS = {
    "US_TV_SHOW": "TV Show — Original Commentary",
    "FEATURE_FILM": "Phim điện ảnh",
    "DE_GERMAN_SOAP": "German Soap",
}
RIGHTS_LABELS = {
    "UNVERIFIED": "Chưa xác minh",
    "OWNED": "Tự sở hữu",
    "LICENSED": "Đã được cấp phép",
    "FIRST_PUBLICATION_RIGHTS": "Có quyền đăng lần đầu",
}


def _code_for_label(mapping: dict[str, str], label: str, fallback: str) -> str:
    return next((code for code, name in mapping.items() if name == label), fallback)


class SettingsDialog(tk.Toplevel):
    """Central settings window for the automatic Toolrecap edition."""

    def __init__(
        self,
        parent: tk.Misc,
        settings: AppSettings,
        store: SettingsStore,
        voice_manager: UnifiedTTSManager,
        *,
        saved: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.store = store
        self.voice_manager = voice_manager
        self.saved = saved or (lambda: None)
        self.voice_map: dict[str, VoiceChoice] = {}
        self._panes: dict[str, ttk.Frame] = {}
        self._nav_buttons: dict[str, ttk.Button] = {}

        self.title("Cài đặt — Toolrecap")
        self.geometry("900x700")
        self.minsize(820, 620)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._make_variables()
        self._build()
        self._show_pane("AI Gateway")
        self._reload_voices()
        self.grab_set()

    def _make_variables(self) -> None:
        value = self.settings
        self.language_var = tk.StringVar(value=LANGUAGE_LABELS.get(value.recap_language, "English (US)"))
        self.mode_var = tk.StringVar(value=MODE_LABELS.get(value.recap_mode, "Nội dung chính"))
        self.content_var = tk.StringVar(value=CONTENT_LABELS.get(value.content_type, CONTENT_LABELS["US_TV_SHOW"]))
        self.rights_var = tk.StringVar(value=RIGHTS_LABELS.get(value.source_rights_status, "Chưa xác minh"))

        self.endpoint_var = tk.StringVar(value=value.api_endpoint)
        self.key_var = tk.StringVar(value=value.api_key)
        self.show_key_var = tk.BooleanVar(value=False)
        self.scanner_model_var = tk.StringVar(value=value.scanner_model)
        self.scanner_thinking_var = tk.StringVar(value=value.scanner_thinking)
        self.finalizer_model_var = tk.StringVar(value=value.finalizer_model)
        self.finalizer_thinking_var = tk.StringVar(value=value.finalizer_thinking)
        self.parallel_var = tk.IntVar(value=value.scanner_parallelism)
        self.chunk_var = tk.IntVar(value=value.api_chunk_seconds)
        self.ai_status_var = tk.StringVar(value="Scanner và Finalizer chưa được kiểm tra.")

        self.voice_var = tk.StringVar()
        self.style_var = tk.StringVar(value=STYLE_NAMES.get(value.voice_style, "Recap phim"))
        self.voice_status_var = tk.StringVar(value="Chọn giọng để nghe thử.")

        quality_labels = {"standard": "Tiêu chuẩn", "high": "Chất lượng cao", "source": "Gần bản gốc"}
        self.quality_var = tk.StringVar(value=quality_labels.get(value.quality, "Chất lượng cao"))
        self.gpu_var = tk.BooleanVar(value=value.use_gpu)
        self.burn_var = tk.BooleanVar(value=value.burn_subtitles)
        self.notify_var = tk.BooleanVar(value=value.notify_complete)
        self.output_var = tk.StringVar(value=value.output_subdirectory)

    def _build(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(root, padding=(0, 4, 12, 4))
        sidebar.grid(row=0, column=0, sticky="ns")
        for name in ("Recap", "AI Gateway", "Giọng đọc", "Render và đầu ra"):
            button = ttk.Button(sidebar, text=name, width=22, command=lambda target=name: self._show_pane(target))
            button.pack(fill="x", pady=3)
            self._nav_buttons[name] = button

        host = ttk.Frame(root, padding=(18, 8))
        host.grid(row=0, column=1, sticky="nsew")
        host.columnconfigure(0, weight=1)
        host.rowconfigure(0, weight=1)

        self._panes["Recap"] = self._build_recap(host)
        self._panes["AI Gateway"] = self._build_ai(host)
        self._panes["Giọng đọc"] = self._build_voice(host)
        self._panes["Render và đầu ra"] = self._build_render(host)
        for pane in self._panes.values():
            pane.grid(row=0, column=0, sticky="nsew")

        bottom = ttk.Frame(root)
        bottom.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(bottom, text="Hủy", command=self.destroy).pack(side="right")
        ttk.Button(bottom, text="Lưu cài đặt", style="Primary.TButton", command=self._save).pack(side="right", padx=(0, 8))

    def _pane(self, parent: ttk.Frame, title: str) -> ttk.Frame:
        frame = ttk.Frame(parent)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text=title, font=("Segoe UI Semibold", 16)).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 18))
        return frame

    def _build_recap(self, parent: ttk.Frame) -> ttk.Frame:
        frame = self._pane(parent, "Cài đặt Recap")
        rows = (
            ("Ngôn ngữ", self.language_var, list(LANGUAGE_LABELS.values())),
            ("Kiểu recap", self.mode_var, list(MODE_LABELS.values())),
            ("Loại nội dung", self.content_var, list(CONTENT_LABELS.values())),
            ("Quyền footage", self.rights_var, list(RIGHTS_LABELS.values())),
        )
        for row, (label, variable, values) in enumerate(rows, start=1):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=5)
            combo = ttk.Combobox(frame, textvariable=variable, values=values, state="readonly")
            combo.grid(row=row, column=1, sticky="ew", padx=(14, 0), pady=5)
            if label == "Ngôn ngữ":
                combo.bind("<<ComboboxSelected>>", lambda _event: self._reload_voices())
        ttk.Label(frame, text="Prompt Recap").grid(row=5, column=0, sticky="nw", pady=(12, 5))
        prompt_box = ttk.Frame(frame)
        prompt_box.grid(row=5, column=1, sticky="nsew", padx=(14, 0), pady=(12, 5))
        prompt_box.columnconfigure(0, weight=1)
        prompt_box.rowconfigure(0, weight=1)
        self.prompt_text = tk.Text(prompt_box, height=18, wrap="word", font=("Consolas", 9), undo=True)
        prompt_scroll = ttk.Scrollbar(prompt_box, orient="vertical", command=self.prompt_text.yview)
        self.prompt_text.configure(yscrollcommand=prompt_scroll.set)
        self.prompt_text.grid(row=0, column=0, sticky="nsew")
        prompt_scroll.grid(row=0, column=1, sticky="ns")
        self.prompt_text.insert("1.0", self.settings.recap_prompt or recap_prompt_for_content(self.settings.content_type))
        ttk.Button(frame, text="Nạp lại prompt mặc định", command=self._load_default_prompt).grid(row=6, column=1, sticky="w", padx=(14, 0), pady=6)
        frame.rowconfigure(5, weight=1)
        return frame

    def _build_ai(self, parent: ttk.Frame) -> ttk.Frame:
        frame = self._pane(parent, "AI Gateway")
        labels = (
            ("API endpoint", self.endpoint_var),
            ("API key", self.key_var),
            ("Scanner model", self.scanner_model_var),
            ("Scanner thinking", self.scanner_thinking_var),
            ("Finalizer model", self.finalizer_model_var),
            ("Finalizer thinking", self.finalizer_thinking_var),
        )
        self.key_entry: ttk.Entry | None = None
        for row, (label, variable) in enumerate(labels, start=1):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=5)
            if "thinking" in label.casefold():
                widget: tk.Widget = ttk.Combobox(
                    frame,
                    textvariable=variable,
                    values=["auto", "low", "medium", "high", "xhigh", "max"],
                    state="readonly",
                )
            else:
                widget = ttk.Entry(frame, textvariable=variable, show="●" if label == "API key" else "")
            widget.grid(row=row, column=1, sticky="ew", padx=(14, 8), pady=5)
            if label == "API key":
                self.key_entry = widget  # type: ignore[assignment]
                ttk.Checkbutton(frame, text="Hiện", variable=self.show_key_var, command=self._toggle_key).grid(row=row, column=2, sticky="w")

        ttk.Label(frame, text="Scanner parallelism").grid(row=7, column=0, sticky="w", pady=5)
        ttk.Spinbox(frame, from_=1, to=4, textvariable=self.parallel_var, width=8).grid(row=7, column=1, sticky="w", padx=(14, 0), pady=5)
        ttk.Label(frame, text="Độ dài đoạn").grid(row=8, column=0, sticky="w", pady=5)
        chunk = ttk.Frame(frame)
        chunk.grid(row=8, column=1, sticky="w", padx=(14, 0), pady=5)
        ttk.Spinbox(chunk, from_=60, to=900, increment=30, textvariable=self.chunk_var, width=10).pack(side="left")
        ttk.Label(chunk, text="giây").pack(side="left", padx=8)

        tests = ttk.Frame(frame)
        tests.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(20, 8))
        tests.columnconfigure((0, 1), weight=1)
        ttk.Button(tests, text="Test Scanner", command=lambda: self._test_api("scanner")).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ttk.Button(tests, text="Test Finalizer", command=lambda: self._test_api("finalizer")).grid(row=0, column=1, sticky="ew", padx=(5, 0))
        ttk.Label(frame, textvariable=self.ai_status_var, foreground="#075fc9", wraplength=570).grid(row=10, column=0, columnspan=3, sticky="w", pady=8)
        return frame

    def _build_voice(self, parent: ttk.Frame) -> ttk.Frame:
        frame = self._pane(parent, "Giọng đọc")
        ttk.Label(frame, text="Ngôn ngữ").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Label(frame, textvariable=self.language_var).grid(row=1, column=1, sticky="w", padx=(14, 0), pady=6)
        ttk.Label(frame, text="Giọng VoiceStudio").grid(row=2, column=0, sticky="w", pady=6)
        self.voice_combo = ttk.Combobox(frame, textvariable=self.voice_var, state="readonly")
        self.voice_combo.grid(row=2, column=1, sticky="ew", padx=(14, 0), pady=6)
        ttk.Label(frame, text="Phong cách").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Combobox(frame, textvariable=self.style_var, values=list(STYLE_NAMES.values()), state="readonly").grid(row=3, column=1, sticky="ew", padx=(14, 0), pady=6)
        actions = ttk.Frame(frame)
        actions.grid(row=4, column=1, sticky="w", padx=(14, 0), pady=16)
        ttk.Button(actions, text="▶ Nghe thử", command=self._preview_voice).pack(side="left")
        ttk.Button(actions, text="■ Dừng nghe", command=self._stop_preview).pack(side="left", padx=8)
        ttk.Label(frame, textvariable=self.voice_status_var, foreground="#075fc9", wraplength=560).grid(row=5, column=0, columnspan=2, sticky="w", pady=8)
        ttk.Label(
            frame,
            text="Bản Toolrecap này chỉ sử dụng các giọng tiếng Anh của VoiceStudio.",
            foreground="#555555",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(20, 0))
        return frame

    def _build_render(self, parent: ttk.Frame) -> ttk.Frame:
        frame = self._pane(parent, "Render và đầu ra")
        ttk.Label(frame, text="Độ phân giải").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Label(frame, text="Giữ nguyên nguồn").grid(row=1, column=1, sticky="w", padx=(14, 0), pady=6)
        ttk.Label(frame, text="Định dạng").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Label(frame, text="H.264 / MP4").grid(row=2, column=1, sticky="w", padx=(14, 0), pady=6)
        ttk.Label(frame, text="Chất lượng").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Combobox(frame, textvariable=self.quality_var, values=["Tiêu chuẩn", "Chất lượng cao", "Gần bản gốc"], state="readonly").grid(row=3, column=1, sticky="ew", padx=(14, 0), pady=6)
        ttk.Checkbutton(frame, text="Dùng GPU", variable=self.gpu_var).grid(row=4, column=1, sticky="w", padx=(14, 0), pady=6)
        ttk.Checkbutton(frame, text="Gắn phụ đề vào video", variable=self.burn_var).grid(row=5, column=1, sticky="w", padx=(14, 0), pady=6)
        ttk.Checkbutton(frame, text="Thông báo khi hoàn tất", variable=self.notify_var).grid(row=6, column=1, sticky="w", padx=(14, 0), pady=6)
        ttk.Label(frame, text="Thư mục đầu ra").grid(row=7, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=self.output_var).grid(row=7, column=1, sticky="ew", padx=(14, 0), pady=6)
        ttk.Label(
            frame,
            text="Mỗi video luôn xuất: MP4 + subtitle narration + subtitle video gốc.",
            foreground="#555555",
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(20, 0))
        return frame

    def _show_pane(self, name: str) -> None:
        self._panes[name].tkraise()
        for key, button in self._nav_buttons.items():
            button.state(["disabled"] if key == name else ["!disabled"])

    def _toggle_key(self) -> None:
        if self.key_entry is not None:
            self.key_entry.configure(show="" if self.show_key_var.get() else "●")

    def _load_default_prompt(self) -> None:
        content_type = _code_for_label(CONTENT_LABELS, self.content_var.get(), "US_TV_SHOW")
        self.prompt_text.delete("1.0", "end")
        self.prompt_text.insert("1.0", recap_prompt_for_content(content_type))

    def _reload_voices(self) -> None:
        language = _code_for_label(LANGUAGE_LABELS, self.language_var.get(), "en-US")
        voices = LocalSpeechClient.compatible_voices(language)
        self.voice_map = {item.display_name: item for item in voices if item.installed}
        self.voice_combo.configure(values=list(self.voice_map))
        selected = next((name for name, item in self.voice_map.items() if item.voice_id == self.settings.voice_id), "")
        if not selected and self.voice_map:
            preferred_id = "voicestudio.en.documentarian" if language == "en-US" else "voicestudio.en.commentator"
            selected = next((name for name, item in self.voice_map.items() if item.voice_id == preferred_id), next(iter(self.voice_map)))
        self.voice_var.set(selected)

    def _preview_voice(self) -> None:
        choice = self.voice_map.get(self.voice_var.get())
        if choice is None:
            messagebox.showwarning("Giọng đọc", "Không có giọng VoiceStudio phù hợp.", parent=self)
            return
        language = _code_for_label(LANGUAGE_LABELS, self.language_var.get(), "en-US")
        style = _code_for_label(STYLE_NAMES, self.style_var.get(), "film_recap")
        self.voice_status_var.set("Đang chuẩn bị mẫu giọng…")

        def work() -> None:
            try:
                path = self.voice_manager.preview(choice.voice_id, language, style)
                self.after(0, lambda: AudioPreviewPlayer.play(path))
                self.after(0, self.voice_status_var.set, f"Đang phát: {choice.name}")
            except Exception as exc:
                self.after(0, lambda value=str(exc): messagebox.showerror("Không thể nghe thử", value, parent=self))
                self.after(0, self.voice_status_var.set, "Nghe thử thất bại.")

        threading.Thread(target=work, daemon=True).start()

    def _stop_preview(self) -> None:
        AudioPreviewPlayer.stop()
        self.voice_status_var.set("Đã dừng nghe thử.")

    def _test_api(self, stage: str) -> None:
        endpoint = self.endpoint_var.get().strip()
        key = self.key_var.get().strip()
        if stage == "scanner":
            model = self.scanner_model_var.get().strip()
            thinking = self.scanner_thinking_var.get().strip()
            label = "Scanner"
        else:
            model = self.finalizer_model_var.get().strip()
            thinking = self.finalizer_thinking_var.get().strip()
            label = "Finalizer"
        if not endpoint or not model:
            messagebox.showwarning("AI Gateway", "Endpoint và model không được để trống.", parent=self)
            return
        self.ai_status_var.set(f"Đang kiểm tra {label} — {model}…")

        def work() -> None:
            started = time.monotonic()
            try:
                result = OpenAICompatibleClient(endpoint, key, timeout=120).test(model, thinking)
                elapsed = time.monotonic() - started
                self.after(0, self.ai_status_var.set, f"{label} hoạt động — {model} / {thinking} — {elapsed:.1f} giây — {result}")
            except Exception as exc:
                self.after(0, self.ai_status_var.set, f"{label} lỗi — {model}: {str(exc)[:500]}")

        threading.Thread(target=work, daemon=True).start()

    def _save(self) -> None:
        try:
            parallel = max(1, min(4, int(self.parallel_var.get())))
            chunk = max(60, min(900, int(self.chunk_var.get())))
        except (tk.TclError, ValueError):
            messagebox.showerror("Cài đặt", "Scanner parallelism hoặc Độ dài đoạn không hợp lệ.", parent=self)
            return
        endpoint = self.endpoint_var.get().strip()
        scanner = self.scanner_model_var.get().strip()
        finalizer = self.finalizer_model_var.get().strip()
        prompt = self.prompt_text.get("1.0", "end").strip()
        if not endpoint or not scanner or not finalizer or not prompt:
            messagebox.showerror("Cài đặt", "Endpoint, hai model và Prompt Recap không được để trống.", parent=self)
            return

        self.settings.recap_language = _code_for_label(LANGUAGE_LABELS, self.language_var.get(), "en-US")
        self.settings.recap_mode = _code_for_label(MODE_LABELS, self.mode_var.get(), "MAIN_STORIES")
        self.settings.content_type = _code_for_label(CONTENT_LABELS, self.content_var.get(), "US_TV_SHOW")
        self.settings.source_rights_status = _code_for_label(RIGHTS_LABELS, self.rights_var.get(), "UNVERIFIED")
        self.settings.api_endpoint = endpoint
        self.settings.api_key = self.key_var.get().strip()
        self.settings.scanner_model = scanner
        self.settings.scanner_thinking = self.scanner_thinking_var.get().strip()
        self.settings.finalizer_model = finalizer
        self.settings.finalizer_thinking = self.finalizer_thinking_var.get().strip()
        self.settings.scanner_parallelism = parallel
        self.settings.api_chunk_seconds = chunk
        self.settings.recap_prompt = prompt
        choice = self.voice_map.get(self.voice_var.get())
        self.settings.voice_id = choice.voice_id if choice else ""
        self.settings.voice_style = _code_for_label(STYLE_NAMES, self.style_var.get(), "film_recap")
        self.settings.quality = {"Tiêu chuẩn": "standard", "Chất lượng cao": "high", "Gần bản gốc": "source"}.get(self.quality_var.get(), "high")
        self.settings.use_gpu = self.gpu_var.get()
        self.settings.burn_subtitles = self.burn_var.get()
        self.settings.generate_srt = True
        self.settings.notify_complete = self.notify_var.get()
        self.settings.output_subdirectory = self.output_var.get().strip() or "recaps_da_render"
        self.store.save(self.settings)
        self.saved()
        self.destroy()

