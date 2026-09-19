from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .audio_preview import AudioPreviewPlayer
from .ai_dialog import AIAnalysisDialog
from .batch import BatchImport, import_recap_json
from .gpu import EncoderStatus, detect_gpu_encoder
from .media import format_duration, open_path, probe_media
from .models import load_project
from .notifications import DesktopNotifier
from .originality import audit_project
from .projects import ProjectQueue, ProjectRecord, ProjectStore
from .settings import AppSettings, SettingsStore
from .tts import LocalSpeechClient, VoiceChoice, load_voice_catalog
from .voice_library import VoiceLibraryDialog
from .voice_system import STYLE_NAMES, UnifiedTTSManager


LANGUAGE_NAMES = {
    "en-US": "English (US)",
    "en-GB": "English (UK)",
}
MODE_NAMES = {"FULL_EPISODE": "Recap cả tập", "MAIN_STORIES": "Nội dung chính"}


class RecapStudioApp(tk.Tk):
    def __init__(
        self,
        *,
        app_title: str = "Recap Studio — Phân tích API và Render",
        locked_content_type: str | None = None,
    ) -> None:
        super().__init__()
        self.app_title = app_title
        self.locked_content_type = locked_content_type
        self.title(app_title)
        self.geometry("1530x900")
        self.minsize(1120, 700)
        try:
            self.state("zoomed")
        except tk.TclError:
            pass
        self.protocol("WM_DELETE_WINDOW", self._close)

        self.settings_store = SettingsStore()
        self.settings = self.settings_store.load()
        if locked_content_type == "BODYCAM" and self.settings.output_subdirectory == "recaps_da_render":
            self.settings.output_subdirectory = "bodycam_da_render"
            self.settings_store.save(self.settings)
        self.project_store = ProjectStore()
        self.projects = self.project_store.load()
        self.batch: BatchImport | None = None
        self.notifier = DesktopNotifier(self)
        self.encoder_status = EncoderStatus(False, "Đang kiểm tra", "", "Đang kiểm tra")
        self.voice_map: dict[str, VoiceChoice] = {}
        self.voice_manager = UnifiedTTSManager()
        self._refresh_pending = False

        self.json_var = tk.StringVar()
        self.media_var = tk.StringVar(value=self.settings.last_media_directory)
        self.output_var = tk.StringVar()
        self.mode_var = tk.StringVar(value=MODE_NAMES["FULL_EPISODE"] if locked_content_type == "BODYCAM" else MODE_NAMES["MAIN_STORIES"])
        self.language_var = tk.StringVar(value="English (US)")
        self.voice_var = tk.StringVar()
        self.style_var = tk.StringVar(value="Documentary" if locked_content_type == "BODYCAM" else "Kể chuyện")
        self.quality_var = tk.StringVar(value={"standard": "Tiêu chuẩn", "high": "Chất lượng cao", "source": "Gần bản gốc"}.get(self.settings.quality, "Chất lượng cao"))
        self.codec_var = tk.StringVar(value="H.264")
        self.srt_var = tk.BooleanVar(value=True)
        self.burn_var = tk.BooleanVar(value=self.settings.burn_subtitles)
        self.gpu_var = tk.BooleanVar(value=self.settings.use_gpu)
        self.aspect_var = tk.BooleanVar(value=True)
        self.notify_var = tk.BooleanVar(value=self.settings.notify_complete)
        self.preview_status_var = tk.StringVar(value="Chọn ngôn ngữ và giọng đọc để nghe thử.")
        self.overall_var = tk.IntVar(value=0)

        self._style()
        self._build()
        self.queue = ProjectQueue(
            self.projects,
            store=self.project_store,
            settings_store=self.settings_store,
            callback=self._worker_update,
            batch_callback=self._worker_batch_finished,
        )
        self._reload_voices("en-US")
        self._refresh_table()
        self.after(100, self._detect_gpu_async)

    def _style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("vista" if "vista" in style.theme_names() else "clam")
        style.configure("Treeview", rowheight=32, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 9), padding=6)
        style.configure("TButton", font=("Segoe UI", 9), padding=(10, 6))
        style.configure("Primary.TButton", font=("Segoe UI Semibold", 10), padding=(18, 9))
        style.configure("TCombobox", padding=4)
        style.configure("Horizontal.TProgressbar", thickness=15)

    def _build(self) -> None:
        main = ttk.Frame(self, padding=12)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(5, weight=1)

        json_label = "File JSON Bodycam" if self.locked_content_type == "BODYCAM" else "File JSON recap"
        media_label = "Video / thư mục Bodycam" if self.locked_content_type == "BODYCAM" else "Thư mục phim"
        analysis_label = "Phân tích Bodycam bằng AI API…" if self.locked_content_type == "BODYCAM" else "Phân tích bằng AI API…"
        ttk.Label(main, text=json_label).grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(main, textvariable=self.json_var, state="readonly").grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(main, text="Chọn JSON…", command=self._choose_json).grid(row=0, column=2, padx=(10, 0), pady=4)

        ttk.Label(main, text=media_label).grid(row=1, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(main, textvariable=self.media_var, state="readonly").grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(main, text="Chọn lại…", command=self._choose_media_root).grid(row=1, column=2, padx=(10, 0), pady=4)

        ttk.Label(main, text="Thư mục kết quả").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(main, textvariable=self.output_var, state="readonly").grid(row=2, column=1, columnspan=2, sticky="ew", pady=4)

        select_bar = ttk.Frame(main)
        select_bar.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 5))
        ttk.Button(select_bar, text="Chọn tất cả", command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(select_bar, text="Bỏ chọn tất cả", command=lambda: self._set_all(False)).pack(side="left", padx=8)
        ttk.Button(select_bar, text="Chạy lại tập lỗi", command=self._retry_failed).pack(side="left")
        ttk.Button(select_bar, text=analysis_label, command=self._open_ai_analysis).pack(side="right")
        self.batch_summary = ttk.Label(select_bar, text="Chưa nhập JSON.")
        self.batch_summary.pack(side="right", padx=(0, 12))

        table_frame = ttk.Frame(main)
        table_frame.grid(row=5, column=0, columnspan=3, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("choose", "episode", "video", "mode", "outputs", "language", "voice", "gpu", "progress", "status")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="extended")
        headings = {
            "choose": "Chọn", "episode": "Tập", "video": "Video nguồn", "mode": "Kiểu recap", "outputs": "Số video",
            "language": "Ngôn ngữ", "voice": "Giọng đọc", "gpu": "GPU", "progress": "Tiến trình", "status": "Trạng thái",
        }
        widths = {"choose": 55, "episode": 85, "video": 235, "mode": 125, "outputs": 70, "language": 115, "voice": 145, "gpu": 110, "progress": 115, "status": 210}
        for key in columns:
            self.tree.heading(key, text=headings[key])
            self.tree.column(key, width=widths[key], minwidth=45, anchor="center" if key not in {"video", "status"} else "w", stretch=key in {"video", "status"})
        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Button-1>", self._table_click)
        self.tree.bind("<<TreeviewSelect>>", self._row_selected)
        self.tree.tag_configure("completed", foreground="#15803d")
        self.tree.tag_configure("failed", foreground="#dc2626")
        self.tree.tag_configure("missing", foreground="#b45309")

        voice = ttk.LabelFrame(main, text="Giọng đọc", padding=10)
        voice.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Label(voice, text="Ngôn ngữ:").grid(row=0, column=0, sticky="w")
        self.language_combo = ttk.Combobox(voice, textvariable=self.language_var, values=list(LANGUAGE_NAMES.values()), state="readonly", width=22)
        self.language_combo.grid(row=0, column=1, padx=(6, 25), sticky="w")
        self.language_combo.bind("<<ComboboxSelected>>", self._language_changed)
        ttk.Label(voice, text="Giọng đọc:").grid(row=0, column=2, sticky="w")
        self.voice_combo = ttk.Combobox(voice, textvariable=self.voice_var, state="readonly", width=34)
        self.voice_combo.grid(row=0, column=3, padx=(6, 25), sticky="ew")
        self.voice_combo.bind("<<ComboboxSelected>>", self._voice_changed)
        ttk.Label(voice, text="Phong cách:").grid(row=0, column=4, sticky="w")
        style_combo = ttk.Combobox(voice, textvariable=self.style_var, values=list(STYLE_NAMES.values()), state="readonly", width=18)
        style_combo.grid(row=0, column=5, padx=(6, 15))
        style_combo.bind("<<ComboboxSelected>>", lambda _event: self._voice_changed())
        ttk.Button(voice, text="Thư viện giọng", command=self._open_voice_library).grid(row=0, column=6, padx=4)
        ttk.Button(voice, text="▶ Nghe thử", command=self._preview_voice).grid(row=0, column=7, padx=4)
        ttk.Button(voice, text="■ Dừng nghe", command=self._stop_preview).grid(row=0, column=8, padx=4)
        ttk.Label(voice, textvariable=self.preview_status_var, foreground="#075fc9").grid(row=1, column=0, columnspan=9, sticky="w", pady=(8, 0))
        voice.columnconfigure(3, weight=1)

        render = ttk.LabelFrame(main, text="Cài đặt render", padding=10)
        render.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Checkbutton(render, text="Giữ nguyên khung hình gốc", variable=self.aspect_var, state="disabled").grid(row=0, column=0, sticky="w")
        ttk.Label(render, text="Độ phân giải:").grid(row=0, column=1, padx=(35, 5))
        resolution = ttk.Combobox(render, values=["Giữ nguyên"], state="readonly", width=15)
        resolution.grid(row=0, column=2)
        resolution.current(0)
        ttk.Label(render, text="Định dạng:").grid(row=0, column=3, padx=(35, 5))
        ttk.Combobox(render, textvariable=self.codec_var, values=["H.264"], state="readonly", width=10).grid(row=0, column=4)
        ttk.Checkbutton(render, text="Dùng GPU", variable=self.gpu_var, command=self._settings_changed).grid(row=0, column=5, padx=(35, 10))
        self.gpu_label = ttk.Label(render, text="Đang kiểm tra GPU…", foreground="#555555")
        self.gpu_label.grid(row=0, column=6, sticky="w")
        ttk.Label(render, text="Chất lượng:").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Combobox(render, textvariable=self.quality_var, values=["Tiêu chuẩn", "Chất lượng cao", "Gần bản gốc"], state="readonly", width=20).grid(row=1, column=1, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Checkbutton(render, text="Luôn tạo 2 file SRT", variable=self.srt_var, state="disabled").grid(row=1, column=3, columnspan=2, pady=(10, 0))
        ttk.Checkbutton(render, text="Gắn phụ đề vào video", variable=self.burn_var, command=self._settings_changed).grid(row=1, column=5, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Checkbutton(render, text="Thông báo khi hoàn tất", variable=self.notify_var, command=self._settings_changed).grid(row=1, column=7, sticky="w", padx=(25, 0), pady=(10, 0))
        ttk.Label(render, text="Kiểu recap:").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.mode_combo = ttk.Combobox(
            render,
            textvariable=self.mode_var,
            values=[MODE_NAMES["FULL_EPISODE"], MODE_NAMES["MAIN_STORIES"]],
            state="readonly",
            width=20,
        )
        self.mode_combo.grid(row=2, column=1, columnspan=2, sticky="w", pady=(10, 0))
        self.mode_combo.bind("<<ComboboxSelected>>", self._mode_changed)

        log_frame = ttk.LabelFrame(main, text="Nhật ký", padding=8)
        log_frame.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self.log_text = tk.Text(log_frame, height=3, wrap="word", state="disabled", relief="flat", background="#ffffff", font=("Segoe UI", 9))
        self.log_text.pack(fill="x")

        bottom = ttk.Frame(main)
        bottom.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Label(bottom, text="Tiến trình tổng:").pack(side="left")
        ttk.Progressbar(bottom, variable=self.overall_var, maximum=100).pack(side="left", fill="x", expand=True, padx=10)
        self.overall_label = ttk.Label(bottom, text="0%")
        self.overall_label.pack(side="left", padx=(0, 18))
        ttk.Button(bottom, text="Bắt đầu render", style="Primary.TButton", command=self._run_enabled).pack(side="left", padx=4)
        ttk.Button(bottom, text="■ Dừng", command=self._stop_all).pack(side="left", padx=4)
        ttk.Button(bottom, text="Mở thư mục kết quả", command=self._open_output).pack(side="left", padx=4)
        ttk.Button(bottom, text="Đóng", command=self._close).pack(side="right", padx=4)

    def _choose_json(self) -> None:
        initial = self.settings.last_json_directory or str(Path.home())
        path = filedialog.askopenfilename(parent=self, title="Chọn file JSON recap", initialdir=initial, filetypes=[("JSON", "*.json")])
        if not path:
            return
        self.json_var.set(path)
        self.settings.last_json_directory = str(Path(path).parent)
        self.settings_store.save(self.settings)
        self._load_batch_async(Path(path), None)

    def _open_ai_analysis(self) -> None:
        AIAnalysisDialog(
            self,
            self.settings,
            self.settings_store,
            initial_source=self.media_var.get(),
            completed=self._ai_analysis_complete,
            locked_content_type=self.locked_content_type,
            dialog_title="Phân tích Bodycam — Evidence-led Commentary" if self.locked_content_type == "BODYCAM" else None,
        )

    def _ai_analysis_complete(self, json_path: Path) -> None:
        self.json_var.set(str(json_path))
        self.settings.last_json_directory = str(json_path.parent)
        self.settings_store.save(self.settings)
        self._load_batch_async(json_path, None)

    def _choose_media_root(self) -> None:
        initial = self.media_var.get() or self.settings.last_media_directory or str(Path.home())
        path = filedialog.askdirectory(parent=self, title="Chọn thư mục phim", initialdir=initial)
        if path and self.json_var.get():
            self.settings.last_media_directory = path
            self.settings_store.save(self.settings)
            self._load_batch_async(Path(self.json_var.get()), Path(path))

    def _load_batch_async(self, json_path: Path, media_root: Path | None) -> None:
        self._log("Đang đọc JSON và tìm video nguồn…")

        def worker() -> None:
            try:
                result = import_recap_json(json_path, media_root)
                if self.locked_content_type:
                    mismatched = [
                        item.episode_id
                        for item in result.episodes
                        if load_project(item.manifest_path).content_type != self.locked_content_type
                    ]
                    if mismatched:
                        raise ValueError(
                            "Bodycam Studio chỉ nhận JSON có content_type=BODYCAM. "
                            "File không đúng loại tại: " + ", ".join(mismatched)
                        )
                else:
                    bodycam_items = [
                        item.episode_id
                        for item in result.episodes
                        if load_project(item.manifest_path).content_type == "BODYCAM"
                    ]
                    if bodycam_items:
                        raise ValueError(
                            "JSON Bodycam phải được mở bằng Bodycam Studio để tránh dùng nhầm "
                            "prompt và cài đặt của Recap Studio."
                        )
                self.after(0, lambda: self._apply_batch(result))
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda: messagebox.showerror("Không thể nhập JSON", message, parent=self))
                self.after(0, lambda: self._log("Lỗi: " + message))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_batch(self, batch: BatchImport) -> None:
        self.batch = batch
        self.media_var.set(str(batch.media_root))
        self.output_var.set(str(batch.output_root))
        self.settings.last_media_directory = str(batch.media_root)
        self.settings_store.save(self.settings)
        records = []
        catalog = load_voice_catalog()
        for episode in batch.episodes:
            choice = self._preferred_voice(catalog, episode.recap_language)
            records.append(
                ProjectRecord.from_episode(
                    episode,
                    output_root=batch.output_root,
                    voice_engine=choice.engine if choice else "",
                    voice_id=choice.voice_id if choice else "",
                    voice_style=self._style_code(),
                    generate_srt=True,
                    burn_subtitles=self.burn_var.get(),
                    quality=self._quality_code(),
                    use_gpu=self.gpu_var.get(),
                    encoder=self.encoder_status.encoder,
                )
            )
        self.queue.replace_projects(records)
        if records:
            self._set_language(records[0].recap_language)
            self._set_mode_options(records[0])
        self._refresh_table()
        self.batch_summary.configure(text=f"Đã tìm thấy {batch.found_count}/{len(batch.episodes)} tập.")
        self._log(f"Đã nhập {len(batch.episodes)} tập; tìm thấy {batch.found_count} video nguồn.")

    @staticmethod
    def _preferred_voice(catalog: list[VoiceChoice], language: str) -> VoiceChoice | None:
        choices = [item for item in catalog if language in item.languages]
        preferred = "voicestudio.en.documentarian" if language == "en-US" else "voicestudio.en.commentator"
        return next((item for item in choices if item.voice_id == preferred and item.installed), next((item for item in choices if item.installed and item.native_language), choices[0] if choices else None))

    def _set_language(self, code: str) -> None:
        self.language_var.set(LANGUAGE_NAMES.get(code, code))
        self._reload_voices(code)

    def _language_code(self) -> str:
        return next((code for code, name in LANGUAGE_NAMES.items() if name == self.language_var.get()), "en-US")

    def _reload_voices(self, language: str) -> None:
        voices = LocalSpeechClient.compatible_voices(language)
        favorites = self.voice_manager.catalog.favorites()
        recent = {item: index for index, item in enumerate(self.voice_manager.catalog.recent())}
        voices.sort(key=lambda item: (item.voice_id not in favorites, not item.native_language, item.quality_tier != "premium_local", recent.get(item.voice_id, 999), item.name.casefold()))
        self.voice_map = {item.display_name: item for item in voices}
        self.voice_combo.configure(values=list(self.voice_map))
        preferred = self._preferred_voice(voices, language)
        self.voice_var.set(preferred.display_name if preferred else (next(iter(self.voice_map), "Không có giọng")))
        self.preview_status_var.set(LocalSpeechClient.preview_text(language))

    def _language_changed(self, _event=None) -> None:
        self._reload_voices(self._language_code())

    def _voice_changed(self, _event=None) -> None:
        choice = self.voice_map.get(self.voice_var.get())
        language = self._language_code()
        if not choice:
            return
        if choice.style_control:
            self.preview_status_var.set("Giọng này hỗ trợ điều khiển phong cách thực bằng AI local.")
        else:
            self.preview_status_var.set("Giọng này dùng phong cách cố định; tool không giả lập cảm xúc bằng đổi tốc độ.")
        selected = self.tree.selection()
        targets = [item for item in self.projects if item.id in selected and item.recap_language == language]
        if not targets:
            targets = [item for item in self.projects if item.recap_language == language]
        for record in targets:
            record.voice_engine = choice.engine
            record.voice_id = choice.voice_id
            record.voice_style = self._style_code()
        self.project_store.save(self.projects)
        self._refresh_table()

    def _mode_code(self) -> str:
        return next((code for code, name in MODE_NAMES.items() if name == self.mode_var.get()), "MAIN_STORIES")

    def _set_mode_options(self, record: ProjectRecord) -> None:
        modes = record.available_recap_modes or [record.recap_mode]
        labels = [MODE_NAMES[item] for item in ("FULL_EPISODE", "MAIN_STORIES") if item in modes]
        self.mode_combo.configure(values=labels)
        self.mode_var.set(MODE_NAMES.get(record.recap_mode, record.recap_mode))

    def _mode_changed(self, _event=None) -> None:
        mode = self._mode_code()
        selected = self.tree.selection()
        targets = [item for item in self.projects if item.id in selected] if selected else list(self.projects)
        changed = 0
        skipped: list[str] = []
        for record in targets:
            available = record.available_recap_modes or [record.recap_mode]
            if mode not in available:
                skipped.append(record.episode_id)
                continue
            record.recap_mode = mode
            record.output_count = int(record.output_counts_by_mode.get(mode, record.output_count))
            changed += 1
        if changed:
            self.project_store.save(self.projects)
            self._refresh_table()
        if skipped:
            self._log("JSON chưa có kiểu recap đã chọn cho: " + ", ".join(skipped))

    def _preview_voice(self) -> None:
        choice = self.voice_map.get(self.voice_var.get())
        language = self._language_code()
        if not choice:
            return
        client = LocalSpeechClient()
        style = self._style_code()
        self.preview_status_var.set("Đang kiểm tra và chuẩn bị mẫu giọng…")

        def worker() -> None:
            try:
                preview = client.manager.preview(choice.voice_id, language, style)
                self.after(0, lambda: AudioPreviewPlayer.play(preview))
                self.after(0, lambda: self.preview_status_var.set("Đang phát: " + client.preview_text(language)))
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda: messagebox.showerror("Không thể nghe thử", message, parent=self))

        threading.Thread(target=worker, daemon=True).start()

    def _open_voice_library(self) -> None:
        choice = self.voice_map.get(self.voice_var.get())
        current_language = self._language_code()
        dialog = VoiceLibraryDialog(self, self.voice_manager, choice.voice_id if choice else "", current_language)
        self.wait_window(dialog)
        if dialog.result:
            target_language = current_language if current_language in dialog.result.supported_languages else dialog.result.language
            self._set_language(target_language)
            selected = next((item for item in self.voice_map.values() if item.voice_id == dialog.result.voice_id), None)
            if selected is None:
                selected = VoiceChoice(
                    key=dialog.result.voice_id,
                    name=dialog.result.label,
                    engine=dialog.result.engine,
                    voice_id=dialog.result.voice_id,
                    languages=dialog.result.supported_languages,
                    license_name=dialog.result.license,
                    source_url=dialog.result.source,
                    note=dialog.result.description,
                    gender=dialog.result.gender,
                    styles=dialog.result.styles,
                    installed=dialog.result.installed,
                    quality_tier=dialog.result.quality_tier,
                    native_language=dialog.result.native_language,
                    style_control=dialog.result.style_control,
                )
                self.voice_map[selected.display_name] = selected
                self.voice_combo.configure(values=list(self.voice_map))
            if selected:
                self.voice_var.set(selected.display_name)
                self._voice_changed()

    def _stop_preview(self) -> None:
        AudioPreviewPlayer.stop()
        self.preview_status_var.set("Đã dừng nghe thử.")

    def _table_click(self, event) -> None:
        row = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if row and column == "#1":
            record = next((item for item in self.projects if item.id == row), None)
            if record and record.status not in {"RUNNING", "QUEUED"} and Path(record.source_video).is_file():
                record.enabled = not record.enabled
                self.project_store.save(self.projects)
                self._refresh_table()

    def _row_selected(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        record = next((item for item in self.projects if item.id == selected[0]), None)
        if record:
            self._set_language(record.recap_language)
            self._set_mode_options(record)
            choice = next((item for item in self.voice_map.values() if item.engine == record.voice_engine and item.voice_id == record.voice_id), None)
            if choice:
                self.voice_var.set(choice.display_name)
            else:
                self.voice_var.set("Giọng cũ đã bị loại. Chọn giọng mới.")
                self.preview_status_var.set("Tập này dùng giọng đã bị loại. Chọn giọng mới trước khi render.")

    def _set_all(self, enabled: bool) -> None:
        for record in self.projects:
            record.enabled = enabled and Path(record.source_video).is_file()
        self.project_store.save(self.projects)
        self._refresh_table()

    def _retry_failed(self) -> None:
        ids = []
        for record in self.projects:
            if record.status == "FAILED":
                record.status = "WAITING"
                record.progress = 0
                record.current_message = "Sẵn sàng chạy lại"
                record.enabled = True
                ids.append(record.id)
        self.project_store.save(self.projects)
        self._refresh_table()
        if ids:
            self.queue.run(ids)

    def _settings_changed(self) -> None:
        self.settings.generate_srt = True
        self.settings.burn_subtitles = self.burn_var.get()
        self.settings.use_gpu = self.gpu_var.get()
        self.settings.notify_complete = self.notify_var.get()
        self.settings.quality = self._quality_code()
        self.settings_store.save(self.settings)
        for record in self.projects:
            if record.status not in {"RUNNING", "QUEUED"}:
                record.generate_srt = True
                record.burn_subtitles = self.burn_var.get()
                record.use_gpu = self.gpu_var.get()
                record.quality = self._quality_code()
                record.encoder = self.encoder_status.encoder if self.gpu_var.get() else "libx264"
        self.project_store.save(self.projects)
        self._refresh_table()

    def _quality_code(self) -> str:
        return {"Tiêu chuẩn": "standard", "Chất lượng cao": "high", "Gần bản gốc": "source"}.get(self.quality_var.get(), "high")

    def _style_code(self) -> str:
        return {"Kể chuyện": "storytelling", "Recap phim": "film_recap", "Documentary": "documentary", "Crime / Thriller": "crime_thriller", "Drama": "drama", "Soap / Emotional": "soap_emotional", "Trung tính": "neutral", "Năng động": "energetic"}.get(self.style_var.get(), "film_recap")

    def _run_enabled(self) -> None:
        ready = [item for item in self.projects if item.enabled and Path(item.source_video).is_file()]
        if not ready:
            messagebox.showinfo("Chưa có tập sẵn sàng", "Hãy nhập JSON và chọn ít nhất một tập có video nguồn.", parent=self)
            return
        if self.gpu_var.get() and not self.encoder_status.available:
            messagebox.showerror("GPU chưa sẵn sàng", "Không tìm thấy GPU encoder hoạt động. Hãy bỏ chọn Dùng GPU nếu muốn render bằng CPU.", parent=self)
            return
        available = {item.voice_id: item for item in self.voice_manager.list_voices(installed_only=True)}
        invalid = [item.episode_id for item in ready if item.voice_id not in available or item.recap_language not in available[item.voice_id].supported_languages]
        if invalid:
            messagebox.showerror("Cần chọn giọng mới", "Các tập đang dùng giọng đã bị loại hoặc không hỗ trợ ngôn ngữ: " + ", ".join(invalid) + ". Chọn các tập rồi chọn giọng mới trước khi render.", parent=self)
            return
        unavailable_modes = [item.episode_id for item in ready if item.recap_mode not in (item.available_recap_modes or [item.recap_mode])]
        if unavailable_modes:
            messagebox.showerror(
                "JSON thiếu kiểu recap",
                "Các tập không có output cho kiểu recap đã chọn: " + ", ".join(unavailable_modes),
                parent=self,
            )
            return
        originality_warnings: list[str] = []
        for item in ready:
            report = audit_project(load_project(item.manifest_path), recap_mode=item.recap_mode)
            if not report.passes:
                details = "; ".join(issue.message for issue in report.blocking_issues[:3])
                originality_warnings.append(f"{item.episode_id}: {details}")
        if originality_warnings:
            proceed = messagebox.askyesno(
                "Cảnh báo nội dung gốc",
                "Các dự án sau chưa đạt bộ kiểm tra originality nội bộ:\n\n"
                + "\n".join(originality_warnings)
                + "\n\nĐây không phải chứng nhận của Facebook. Bạn vẫn muốn render?",
                parent=self,
            )
            if not proceed:
                return
        self._settings_changed()
        self._log(f"Bắt đầu render {len(ready)} tập bằng {ready[0].encoder or 'CPU'}.")
        self.queue.run([item.id for item in ready])

    def _stop_all(self) -> None:
        self.queue.stop_all()
        self._log("Đã gửi yêu cầu dừng an toàn.")

    def _open_output(self) -> None:
        path: Path | None = None
        selected = self.tree.selection()
        if selected:
            record = next((item for item in self.projects if item.id == selected[0]), None)
            if record and record.output_directory:
                path = Path(record.output_directory)
        if path is None:
            completed = [item for item in self.projects if item.status == "COMPLETED" and item.output_directory]
            if len(completed) == 1:
                path = Path(completed[0].output_directory)
        if path is None and self.output_var.get():
            path = Path(self.output_var.get())
        if path is not None:
            try:
                path.mkdir(parents=True, exist_ok=True)
                open_path(path)
            except Exception as exc:
                messagebox.showerror("Không thể mở thư mục kết quả", str(exc), parent=self)

    def _detect_gpu_async(self) -> None:
        def worker() -> None:
            status = detect_gpu_encoder(refresh=True)
            self.after(0, lambda: self._apply_gpu_status(status))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_gpu_status(self, status: EncoderStatus) -> None:
        self.encoder_status = status
        if status.available:
            self.gpu_label.configure(text=f"{status.gpu_name} — {status.encoder} — Sẵn sàng", foreground="#15803d")
        else:
            self.gpu_label.configure(text=f"{status.gpu_name} — GPU chưa sẵn sàng", foreground="#b45309")
        for record in self.projects:
            record.encoder = status.encoder if self.gpu_var.get() else "libx264"
        self._refresh_table()

    def _refresh_table(self) -> None:
        known = {item.id for item in self.projects}
        for iid in self.tree.get_children():
            if iid not in known:
                self.tree.delete(iid)
        for record in self.projects:
            source_exists = Path(record.source_video).is_file()
            tags = []
            if record.status == "COMPLETED":
                tags.append("completed")
            elif record.status == "FAILED":
                tags.append("failed")
            elif not source_exists:
                tags.append("missing")
            values = (
                "☑" if record.enabled else "☐",
                record.episode_id,
                Path(record.source_video).name if source_exists else "Chưa tìm thấy",
                MODE_NAMES.get(record.recap_mode, record.recap_mode),
                record.output_count,
                LANGUAGE_NAMES.get(record.recap_language, record.recap_language),
                record.voice_id or "Chưa chọn",
                record.encoder or "Đang kiểm tra",
                f"{record.progress}%",
                record.current_message,
            )
            if self.tree.exists(record.id):
                self.tree.item(record.id, values=values, tags=tags)
            else:
                self.tree.insert("", "end", iid=record.id, values=values, tags=tags)
        enabled = [item for item in self.projects if item.enabled]
        progress = int(sum(item.progress for item in enabled) / len(enabled)) if enabled else 0
        self.overall_var.set(progress)
        self.overall_label.configure(text=f"{progress}%")

    def _worker_update(self, _record: ProjectRecord) -> None:
        try:
            self.after(0, self._schedule_refresh)
        except tk.TclError:
            pass

    def _schedule_refresh(self) -> None:
        if not self._refresh_pending:
            self._refresh_pending = True
            self.after(150, self._flush_refresh)

    def _flush_refresh(self) -> None:
        self._refresh_pending = False
        self._refresh_table()

    def _worker_batch_finished(self, completed: int, failed: int) -> None:
        try:
            self.after(0, lambda: self._batch_finished(completed, failed))
        except tk.TclError:
            pass

    def _batch_finished(self, completed: int, failed: int) -> None:
        self._refresh_table()
        self._log(f"Tiến trình đã hoàn tất: {completed} tập thành công, {failed} tập lỗi.")
        if self.settings.notify_complete or (failed and self.settings.notify_error):
            finished = [item for item in self.projects if item.status == "COMPLETED" and item.output_directory]
            result_path = (
                Path(finished[0].output_directory)
                if len(finished) == 1
                else (Path(self.output_var.get()) if self.output_var.get() else None)
            )
            self.notifier.complete(completed, failed, result_path)

    def _log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _close(self) -> None:
        if any(item.status in {"RUNNING", "QUEUED"} for item in self.projects):
            close_title = "Đóng Bodycam Studio" if self.locked_content_type == "BODYCAM" else "Đóng Recap Studio"
            if not messagebox.askyesno(close_title, "Có dự án đang chạy. Dừng an toàn và đóng ứng dụng?", parent=self):
                return
        self.queue.stop_all()
        AudioPreviewPlayer.stop()
        self.voice_manager.shutdown_all()
        self.project_store.save(self.projects)
        self.destroy()


def run_app(*, app_title: str = "Recap Studio — Phân tích API và Render", locked_content_type: str | None = None) -> None:
    RecapStudioApp(app_title=app_title, locked_content_type=locked_content_type).mainloop()
