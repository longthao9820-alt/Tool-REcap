from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from .api_client import OpenAICompatibleClient
from .recap_api import analyze_input, default_recap_prompt, recap_prompt_for_content
from .settings import AppSettings, SettingsStore


THINKING_LEVELS = ("auto", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
LANGUAGES = {"English (US)": "en-US", "English (UK)": "en-GB"}
MODES = {"Nội dung chính": "MAIN_STORIES", "Recap cả tập": "FULL_EPISODE"}
CONTENT_TYPES = {
    "TV Show — Original Commentary": "US_TV_SHOW",
}
RIGHTS_STATUSES = {
    "Chưa xác minh": "UNVERIFIED",
    "Tôi sở hữu footage": "OWNED",
    "Đã được cấp phép": "LICENSED",
    "Có quyền phát hành lần đầu": "FIRST_PUBLICATION_RIGHTS",
}
BODYCAM_CONTENT_LABEL = "Bodycam — bình luận bằng chứng"
TVSHOW_PROMPT_VERSION = "tvshow-retention-recap-v3-smooth-cuts"


class AIAnalysisDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        settings: AppSettings,
        settings_store: SettingsStore,
        *,
        initial_source: str,
        completed: Callable[[Path], None],
        locked_content_type: str | None = None,
        dialog_title: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.locked_content_type = locked_content_type
        self.title(dialog_title or "Phân tích Recap bằng AI API")
        self.geometry("980x790")
        self.minsize(760, 620)
        self.transient(parent)
        self.settings, self.settings_store, self.completed_callback = settings, settings_store, completed
        self.cancel_event = threading.Event()

        self.source_var = tk.StringVar(value=initial_source)
        self.project_var = tk.StringVar()
        self.endpoint_var = tk.StringVar(value=settings.api_endpoint)
        self.key_var = tk.StringVar(value=settings.api_key)
        self.model_var = tk.StringVar(value=settings.api_model)
        self.thinking_var = tk.StringVar(value=settings.api_thinking)
        self.parallel_var = tk.IntVar(value=settings.api_parallelism)
        self.chunk_var = tk.IntVar(value=settings.api_chunk_seconds)
        self.language_var = tk.StringVar(value="English (US)")
        self.mode_var = tk.StringVar(value="Recap cả tập" if locked_content_type == "BODYCAM" else "Nội dung chính")
        content_label = (
            BODYCAM_CONTENT_LABEL
            if locked_content_type == "BODYCAM"
            else next((label for label, code in CONTENT_TYPES.items() if code == locked_content_type), "TV Show — Original Commentary")
        )
        self.content_var = tk.StringVar(value=content_label)
        self.rights_var = tk.StringVar(value="Chưa xác minh")
        self.status_var = tk.StringVar(value="Sẵn sàng — gọi API trực tiếp")
        self._build()
        if locked_content_type:
            initial_prompt = settings.recap_prompt or recap_prompt_for_content(locked_content_type)
        elif settings.recap_prompt_version == TVSHOW_PROMPT_VERSION and settings.recap_prompt:
            initial_prompt = settings.recap_prompt
        else:
            # Migrate installations that saved the former plot-summary prompt.
            initial_prompt = default_recap_prompt()
        self.prompt_text.insert("1.0", initial_prompt)
        built_in_prompts = {
            default_recap_prompt(),
            recap_prompt_for_content("DE_GERMAN_SOAP"),
            recap_prompt_for_content("BODYCAM"),
        }
        self._auto_prompt = initial_prompt if initial_prompt in built_in_prompts else None
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(7, weight=1)
        ttk.Label(root, text="Video / thư mục phim").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(root, textvariable=self.source_var).grid(row=0, column=1, sticky="ew", padx=8, pady=3)
        choices = ttk.Frame(root)
        choices.grid(row=0, column=2)
        ttk.Button(choices, text="File…", command=self._choose_file).pack(side="left")
        ttk.Button(choices, text="Thư mục…", command=self._choose_folder).pack(side="left", padx=(5, 0))
        ttk.Label(root, text="Tên dự án").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(root, textvariable=self.project_var).grid(row=1, column=1, columnspan=2, sticky="ew", padx=8, pady=3)

        options = ttk.Frame(root)
        options.grid(row=2, column=0, columnspan=3, sticky="ew", pady=5)
        for label, variable, values, width in (
            ("Ngôn ngữ", self.language_var, list(LANGUAGES), 18),
            ("Kiểu recap", self.mode_var, list(MODES), 18),
            ("Loại nội dung", self.content_var, list(CONTENT_TYPES), 20),
            ("Quyền footage", self.rights_var, list(RIGHTS_STATUSES), 24),
        ):
            ttk.Label(options, text=label).pack(side="left", padx=(0, 4))
            content_values = values
            content_state = "readonly"
            if variable is self.content_var and self.locked_content_type:
                content_values = [self.content_var.get()]
                content_state = "disabled"
            combo = ttk.Combobox(options, textvariable=variable, values=content_values, state=content_state, width=width)
            combo.pack(side="left", padx=(0, 16))
            if variable is self.content_var:
                self.content_combo = combo
                combo.bind("<<ComboboxSelected>>", self._content_type_changed)

        api = ttk.LabelFrame(root, text="API OpenAI-compatible — gọi trực tiếp", padding=8)
        api.grid(row=3, column=0, columnspan=3, sticky="ew", pady=4)
        api.columnconfigure(1, weight=1)
        ttk.Label(api, text="Endpoint").grid(row=0, column=0, sticky="w")
        ttk.Entry(api, textvariable=self.endpoint_var).grid(row=0, column=1, columnspan=5, sticky="ew", padx=6, pady=2)
        ttk.Label(api, text="API key").grid(row=1, column=0, sticky="w")
        self.key_entry = ttk.Entry(api, textvariable=self.key_var, show="•")
        self.key_entry.grid(row=1, column=1, sticky="ew", padx=6, pady=2)
        ttk.Checkbutton(api, text="Hiện", command=self._toggle_key).grid(row=1, column=2)
        ttk.Label(api, text="Model").grid(row=2, column=0, sticky="w")
        ttk.Entry(api, textvariable=self.model_var).grid(row=2, column=1, sticky="ew", padx=6, pady=2)
        ttk.Label(api, text="Thinking").grid(row=2, column=2, padx=(10, 3))
        ttk.Combobox(api, textvariable=self.thinking_var, values=THINKING_LEVELS, state="readonly", width=9).grid(row=2, column=3)
        ttk.Label(api, text="Song song").grid(row=2, column=4, padx=(10, 3))
        ttk.Spinbox(api, from_=1, to=4, textvariable=self.parallel_var, width=4).grid(row=2, column=5)
        ttk.Label(api, text="Độ dài đoạn").grid(row=3, column=0, sticky="w")
        ttk.Spinbox(api, from_=60, to=900, increment=30, textvariable=self.chunk_var, width=8).grid(row=3, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(api, text="giây (ảnh và phụ đề được chia đoạn trước khi gửi API)").grid(row=3, column=1, columnspan=5, sticky="w", padx=(80, 0))

        ttk.Label(root, text="Prompt Recap riêng (không dùng chung với Prompt Highlight)").grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 3))
        prompt_frame = ttk.Frame(root)
        prompt_frame.grid(row=5, column=0, columnspan=3, sticky="nsew")
        prompt_frame.rowconfigure(0, weight=1)
        prompt_frame.columnconfigure(0, weight=1)
        self.prompt_text = tk.Text(prompt_frame, wrap="word", height=14, undo=True, font=("Consolas", 9))
        self.prompt_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(prompt_frame, orient="vertical", command=self.prompt_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.prompt_text.configure(yscrollcommand=scroll.set)

        ttk.Label(root, textvariable=self.status_var).grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 3))
        self.log_text = tk.Text(root, height=7, wrap="word", state="disabled", background="#ffffff")
        self.log_text.grid(row=7, column=0, columnspan=3, sticky="nsew")
        buttons = ttk.Frame(root)
        buttons.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.test_button = ttk.Button(buttons, text="Kiểm tra API", command=self._test_api)
        self.test_button.pack(side="left")
        self.start_button = ttk.Button(buttons, text="Bắt đầu phân tích", command=self._start)
        self.start_button.pack(side="left", padx=6)
        self.cancel_button = ttk.Button(buttons, text="Dừng", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="left")
        ttk.Button(buttons, text="Đóng", command=self._close).pack(side="right")

    def _choose_file(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Chọn video", filetypes=[("Video", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts"), ("Tất cả", "*.*")])
        if path:
            self.source_var.set(path)

    def _choose_folder(self) -> None:
        path = filedialog.askdirectory(parent=self, title="Chọn thư mục phim")
        if path:
            self.source_var.set(path)

    def _toggle_key(self) -> None:
        self.key_entry.configure(show="" if self.key_entry.cget("show") else "•")

    def _content_type_changed(self, _event: object | None = None) -> None:
        content_type = self.locked_content_type or CONTENT_TYPES.get(self.content_var.get(), "US_TV_SHOW")
        if self._auto_prompt is None:
            return
        current = self.prompt_text.get("1.0", "end-1c").strip()
        if current != self._auto_prompt.strip():
            # Người dùng đã chỉnh prompt thủ công; không ghi đè.
            self._auto_prompt = None
            return
        selected_prompt = recap_prompt_for_content(content_type)
        self.prompt_text.delete("1.0", "end")
        self.prompt_text.insert("1.0", selected_prompt)
        self._auto_prompt = selected_prompt

    def _save(self) -> None:
        self.settings.api_endpoint = self.endpoint_var.get().strip()
        self.settings.api_key = self.key_var.get().strip()
        self.settings.api_model = self.model_var.get().strip()
        self.settings.api_thinking = self.thinking_var.get()
        self.settings.api_parallelism = max(1, min(4, int(self.parallel_var.get())))
        # The original dialog intentionally remains a one-model workflow.
        # Keep both new stages in sync so running the legacy source behaves
        # exactly as its UI states.
        self.settings.scanner_model = self.settings.api_model
        self.settings.scanner_thinking = self.settings.api_thinking
        self.settings.finalizer_model = self.settings.api_model
        self.settings.finalizer_thinking = self.settings.api_thinking
        self.settings.scanner_parallelism = self.settings.api_parallelism
        self.settings.api_chunk_seconds = max(60, min(900, int(self.chunk_var.get())))
        self.settings.recap_prompt = self.prompt_text.get("1.0", "end-1c").strip()
        if not self.locked_content_type:
            self.settings.recap_prompt_version = TVSHOW_PROMPT_VERSION
        self.settings_store.save(self.settings)

    def _running(self, active: bool) -> None:
        self.test_button.configure(state="disabled" if active else "normal")
        self.start_button.configure(state="disabled" if active else "normal")
        self.cancel_button.configure(state="normal" if active else "disabled")

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _log(self, message: str) -> None:
        self.after(0, self._append_log, message)

    def _test_api(self) -> None:
        self._save()
        self._running(True)
        self.status_var.set("Đang kiểm tra API…")
        def work() -> None:
            try:
                result = OpenAICompatibleClient(self.settings.api_endpoint, self.settings.api_key, timeout=120).test(self.settings.api_model, self.settings.api_thinking)
                self.after(0, lambda: messagebox.showinfo("AI API", result, parent=self))
                self.after(0, self.status_var.set, "API hoạt động")
            except Exception as exc:
                self.after(0, lambda value=str(exc): messagebox.showerror("Lỗi API", value, parent=self))
                self.after(0, self.status_var.set, "API không hoạt động")
            finally:
                self.after(0, self._running, False)
        threading.Thread(target=work, daemon=True).start()

    def _start(self) -> None:
        source = Path(self.source_var.get().strip()).expanduser()
        if not source.exists():
            messagebox.showwarning("Nguồn phim", "Hãy chọn video hoặc thư mục phim hợp lệ.", parent=self)
            return
        self._save()
        if not self.settings.api_endpoint or not self.settings.api_model or not self.settings.recap_prompt:
            messagebox.showwarning("AI API", "Endpoint, model và Prompt Recap không được để trống.", parent=self)
            return
        self.cancel_event.clear()
        self._running(True)
        self.status_var.set("Đang tạo evidence và gọi API trực tiếp…")
        def progress(done: int, total: int, text: str) -> None:
            self.after(0, self.status_var.set, f"{text} ({done}/{total})")
        def work() -> None:
            try:
                result = analyze_input(
                    source,
                    project_name=self.project_var.get(),
                    language=LANGUAGES[self.language_var.get()],
                    mode=MODES[self.mode_var.get()],
                    content_type=self.locked_content_type or CONTENT_TYPES[self.content_var.get()],
                    settings=self.settings,
                    prompt=self.settings.recap_prompt,
                    source_rights_status=RIGHTS_STATUSES[self.rights_var.get()],
                    log=self._log,
                    progress=progress,
                    cancel_event=self.cancel_event,
                )
                self.after(0, self._finished, result)
            except Exception as exc:
                self.after(0, self._failed, str(exc))
        threading.Thread(target=work, daemon=True).start()

    def _finished(self, path: Path) -> None:
        self._running(False)
        self.status_var.set(f"JSON hợp lệ: {path}")
        self.completed_callback(path)
        messagebox.showinfo("Phân tích hoàn tất", f"JSON đã được kiểm tra và nạp vào Recap Studio:\n{path}", parent=self)

    def _failed(self, message: str) -> None:
        self._running(False)
        self.status_var.set("Phân tích thất bại")
        messagebox.showerror("Không thể tạo JSON Recap", message, parent=self)

    def _cancel(self) -> None:
        self.cancel_event.set()
        self.status_var.set("Đang dừng…")

    def _close(self) -> None:
        if str(self.cancel_button.cget("state")) == "normal":
            if not messagebox.askyesno("Đang phân tích", "Dừng phân tích và đóng cửa sổ?", parent=self):
                return
            self.cancel_event.set()
        self.destroy()
