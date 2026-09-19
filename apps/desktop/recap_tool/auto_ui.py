from __future__ import annotations

import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .audio_preview import AudioPreviewPlayer
from .batch import BatchImport, import_recap_json
from .credentials import default_data_directory
from .gpu import EncoderStatus, detect_gpu_encoder
from .media import open_path
from .notifications import DesktopNotifier
from .projects import ProjectQueue, ProjectRecord, ProjectStore
from .recap_api import analyze_input, enumerate_videos, recap_prompt_for_content
from .settings import AppSettings, SettingsStore
from .settings_dialog import SettingsDialog
from .tts import LocalSpeechClient, VoiceChoice
from .voice_system import UnifiedTTSManager


class ToolrecapAutoApp(tk.Tk):
    """One-click Scanner -> Finalizer -> render desktop workflow."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Toolrecap — Tự động")
        self.geometry("1120x760")
        self.minsize(900, 650)
        self.protocol("WM_DELETE_WINDOW", self._close)

        data = default_data_directory()
        self.settings_store = SettingsStore(data / "settings-auto.json")
        if self.settings_store.path.is_file():
            self.settings = self.settings_store.load()
        else:
            # Seed the new edition with endpoint/key/render choices from the
            # original app, but keep future saves completely separate.
            self.settings = SettingsStore().load()
            self.settings.scanner_model = "sub"
            self.settings.scanner_thinking = "max"
            self.settings.finalizer_model = "prime"
            self.settings.finalizer_thinking = "high"
            self.settings.scanner_parallelism = max(1, min(4, self.settings.api_parallelism))
            self.settings_store.save(self.settings)

        self.project_store = ProjectStore(data / "projects-auto.json")
        self.projects: list[ProjectRecord] = []
        self.batch: BatchImport | None = None
        self.source: Path | None = None
        self.source_videos: list[Path] = []
        self.analysis_cancel = threading.Event()
        self.analyzing = False
        self.rendering = False
        self.encoder_status = EncoderStatus(False, "Đang kiểm tra", "", "Đang kiểm tra")
        self.voice_manager = UnifiedTTSManager()
        self.notifier = DesktopNotifier(self)
        self._refresh_pending = False

        self.source_var = tk.StringVar(value="Chưa chọn file hoặc thư mục")
        self.status_var = tk.StringVar(value="Sẵn sàng")
        self.detail_var = tk.StringVar(value="Chọn nguồn phim để bắt đầu.")
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_label_var = tk.StringVar(value="0%")

        self._style()
        self._build()
        self.queue = ProjectQueue(
            self.projects,
            store=self.project_store,
            settings_store=self.settings_store,
            callback=self._worker_update,
            batch_callback=self._worker_batch_finished,
        )
        self.after(100, self._detect_gpu)

    def _style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("vista" if "vista" in style.theme_names() else "clam")
        style.configure("TButton", font=("Segoe UI", 9), padding=(12, 7))
        style.configure("Primary.TButton", font=("Segoe UI Semibold", 11), padding=(20, 11))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 18))
        style.configure("Status.TLabel", font=("Segoe UI Semibold", 11))
        style.configure("Treeview", rowheight=34, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 9), padding=7)
        style.configure("Horizontal.TProgressbar", thickness=16)

    def _build(self) -> None:
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        ttk.Label(header, text="Toolrecap", style="Title.TLabel").pack(side="left")
        self.settings_button = ttk.Button(header, text="⚙  Cài đặt", command=self._open_settings)
        self.settings_button.pack(side="right")

        source_box = ttk.LabelFrame(root, text="Nguồn phim", padding=14)
        source_box.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        source_box.columnconfigure(0, weight=1)
        ttk.Label(
            source_box,
            text="Chọn một file phim hoặc thư mục chứa các tập phim",
            font=("Segoe UI", 11),
        ).grid(row=0, column=0, columnspan=3, pady=(4, 12))
        actions = ttk.Frame(source_box)
        actions.grid(row=1, column=0, columnspan=3)
        self.file_button = ttk.Button(actions, text="Chọn file…", command=self._choose_file)
        self.file_button.pack(side="left", padx=5)
        self.folder_button = ttk.Button(actions, text="Chọn thư mục…", command=self._choose_folder)
        self.folder_button.pack(side="left", padx=5)
        ttk.Label(source_box, textvariable=self.source_var, foreground="#075fc9").grid(
            row=2, column=0, columnspan=3, sticky="ew", pady=(14, 2)
        )

        list_frame = ttk.LabelFrame(root, text="Các tập sẽ xử lý", padding=8)
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        columns = ("episode", "file", "stage", "progress", "status")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "episode": "Tập",
            "file": "Video nguồn",
            "stage": "Giai đoạn",
            "progress": "Tiến trình",
            "status": "Trạng thái",
        }
        widths = {"episode": 100, "file": 320, "stage": 150, "progress": 100, "status": 330}
        for key in columns:
            self.tree.heading(key, text=headings[key])
            self.tree.column(key, width=widths[key], anchor="w" if key in {"file", "status"} else "center", stretch=key in {"file", "status"})
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.tag_configure("completed", foreground="#15803d")
        self.tree.tag_configure("failed", foreground="#dc2626")
        self.tree.tag_configure("running", foreground="#075fc9")

        status = ttk.LabelFrame(root, text="Tiến trình", padding=12)
        status.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self.status_var, style="Status.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(status, textvariable=self.detail_var, foreground="#4b5563").grid(row=1, column=0, sticky="w", pady=(3, 9))
        progress_row = ttk.Frame(status)
        progress_row.grid(row=2, column=0, sticky="ew")
        progress_row.columnconfigure(0, weight=1)
        ttk.Progressbar(progress_row, variable=self.progress_var, maximum=100).grid(row=0, column=0, sticky="ew")
        ttk.Label(progress_row, textvariable=self.progress_label_var, width=6, anchor="e").grid(row=0, column=1, padx=(10, 0))

        controls = ttk.Frame(root)
        controls.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        self.start_button = ttk.Button(
            controls,
            text="▶  Bắt đầu tạo Video Recap",
            style="Primary.TButton",
            command=self._start,
        )
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text="■  Dừng", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.open_button = ttk.Button(controls, text="Mở thư mục kết quả", command=self._open_output)
        self.open_button.pack(side="right")

    def _choose_file(self) -> None:
        if self._busy():
            return
        initial = self.settings.last_media_directory or str(Path.home())
        path = filedialog.askopenfilename(
            parent=self,
            title="Chọn file phim",
            initialdir=initial,
            filetypes=[("Video", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts"), ("Tất cả", "*.*")],
        )
        if path:
            self._set_source(Path(path))

    def _choose_folder(self) -> None:
        if self._busy():
            return
        initial = self.settings.last_media_directory or str(Path.home())
        path = filedialog.askdirectory(parent=self, title="Chọn thư mục phim", initialdir=initial)
        if path:
            self._set_source(Path(path))

    def _set_source(self, source: Path) -> None:
        videos = enumerate_videos(source)
        if not videos:
            messagebox.showerror("Nguồn phim", "Không tìm thấy file video được hỗ trợ.", parent=self)
            return
        self.source = source.resolve()
        self.source_videos = videos
        self.projects.clear()
        self.batch = None
        self.settings.last_media_directory = str(self.source if self.source.is_dir() else self.source.parent)
        self.settings_store.save(self.settings)
        self.source_var.set(str(self.source))
        self.status_var.set(f"Đã tìm thấy {len(videos)} tập")
        self.detail_var.set("Nhấn Bắt đầu tạo Video Recap để phân tích và render tự động.")
        self._set_progress(0)
        self._refresh_table()

    def _open_settings(self) -> None:
        if self._busy():
            messagebox.showinfo("Cài đặt", "Hãy dừng hoặc chờ tiến trình hiện tại hoàn tất.", parent=self)
            return
        SettingsDialog(
            self,
            self.settings,
            self.settings_store,
            self.voice_manager,
            saved=self._settings_saved,
        )

    def _settings_saved(self) -> None:
        self.status_var.set("Đã lưu cài đặt")
        self.detail_var.set(
            f"Scanner: {self.settings.scanner_model} • Finalizer: {self.settings.finalizer_model}"
        )
        self._detect_gpu()

    def _start(self) -> None:
        if self.source is None or not self.source_videos:
            messagebox.showinfo("Chưa chọn nguồn", "Hãy chọn một file hoặc thư mục phim.", parent=self)
            return
        if self._busy():
            return
        if not self.settings.api_endpoint or not self.settings.scanner_model or not self.settings.finalizer_model:
            messagebox.showerror("AI Gateway", "Hãy cấu hình Endpoint, Scanner model và Finalizer model.", parent=self)
            return
        voice = self._selected_voice()
        if voice is None:
            messagebox.showerror("Giọng đọc", "Không tìm thấy giọng VoiceStudio phù hợp. Hãy mở Cài đặt và chọn giọng.", parent=self)
            return
        if self.settings.use_gpu and not self.encoder_status.available:
            messagebox.showerror(
                "GPU chưa sẵn sàng",
                "Không tìm thấy GPU encoder hoạt động. Hãy tắt Dùng GPU trong Cài đặt nếu muốn render bằng CPU.",
                parent=self,
            )
            return

        self.analysis_cancel.clear()
        self.analyzing = True
        self.rendering = False
        self._set_running_controls(True)
        self.status_var.set("Đang chuẩn bị dữ liệu")
        self.detail_var.set(
            f"Scanner: {self.settings.scanner_model} • Finalizer: {self.settings.finalizer_model}"
        )
        self._set_progress(2)
        self._refresh_table()

        source = self.source
        project_name = source.stem if source.is_file() else source.name
        prompt = self.settings.recap_prompt or recap_prompt_for_content(self.settings.content_type)

        def progress(done: int, total: int, message: str) -> None:
            ratio = done / max(total, 1)
            match = re.search(r"(\d+)/(\d+) đoạn", message)
            if match:
                chunk_done, chunk_total = int(match.group(1)), max(1, int(match.group(2)))
                ratio = (done + chunk_done / chunk_total) / max(total, 1)
            value = 4 + min(1.0, ratio) * 41
            self.after(0, self._analysis_progress, value, message)

        def log(message: str) -> None:
            self.after(0, self.detail_var.set, message)

        def work() -> None:
            try:
                output = analyze_input(
                    source,
                    project_name=project_name,
                    language=self.settings.recap_language,
                    mode=self.settings.recap_mode,
                    content_type=self.settings.content_type,
                    settings=self.settings,
                    prompt=prompt,
                    log=log,
                    progress=progress,
                    cancel_event=self.analysis_cancel,
                    source_rights_status=self.settings.source_rights_status,
                )
                batch = import_recap_json(output, source if source.is_dir() else source.parent)
                self.after(0, self._analysis_complete, output, batch)
            except Exception as exc:
                self.after(0, self._analysis_failed, str(exc))

        threading.Thread(target=work, daemon=True, name="toolrecap-analysis").start()

    def _analysis_progress(self, value: float, message: str) -> None:
        self._set_progress(value)
        self.status_var.set("Đang phân tích bằng AI")
        self.detail_var.set(message)
        self._refresh_table()

    def _analysis_complete(self, json_path: Path, batch: BatchImport) -> None:
        self.analyzing = False
        self.batch = batch
        self.settings.last_json_directory = str(json_path.parent)
        self.settings_store.save(self.settings)
        try:
            records = self._records_for_batch(batch)
        except Exception as exc:
            self._analysis_failed(str(exc))
            return
        self.queue.replace_projects(records)
        self.rendering = True
        self.status_var.set("Phân tích hoàn thành — đang tự động render")
        self.detail_var.set(f"Đã tạo JSON hợp lệ: {json_path.name}")
        self._set_progress(46)
        self._refresh_table()
        self.queue.run([item.id for item in records if item.enabled])

    def _analysis_failed(self, message: str) -> None:
        self.analyzing = False
        self.rendering = False
        self._set_running_controls(False)
        self.status_var.set("Phân tích thất bại")
        self.detail_var.set(" ".join(message.split())[:600])
        self.notifier.error(" ".join(message.split())[:240])
        messagebox.showerror("Không thể tạo Video Recap", message, parent=self)
        self._refresh_table()

    def _records_for_batch(self, batch: BatchImport) -> list[ProjectRecord]:
        choice = self._selected_voice()
        if choice is None:
            raise RuntimeError("Không có giọng VoiceStudio phù hợp với ngôn ngữ recap.")
        encoder = self.encoder_status.encoder if self.settings.use_gpu else "libx264"
        return [
            ProjectRecord.from_episode(
                episode,
                output_root=batch.output_root,
                voice_engine=choice.engine,
                voice_id=choice.voice_id,
                voice_style=self.settings.voice_style,
                generate_srt=True,
                burn_subtitles=self.settings.burn_subtitles,
                quality=self.settings.quality,
                use_gpu=self.settings.use_gpu,
                encoder=encoder,
            )
            for episode in batch.episodes
        ]

    def _selected_voice(self) -> VoiceChoice | None:
        choices = [item for item in LocalSpeechClient.compatible_voices(self.settings.recap_language) if item.installed]
        selected = next((item for item in choices if item.voice_id == self.settings.voice_id), None)
        if selected is None:
            preferred = "voicestudio.en.documentarian" if self.settings.recap_language == "en-US" else "voicestudio.en.commentator"
            selected = next((item for item in choices if item.voice_id == preferred), choices[0] if choices else None)
            if selected is not None:
                self.settings.voice_id = selected.voice_id
                self.settings_store.save(self.settings)
        return selected

    def _worker_update(self, _record: ProjectRecord) -> None:
        try:
            self.after(0, self._schedule_refresh)
        except tk.TclError:
            pass

    def _schedule_refresh(self) -> None:
        if not self._refresh_pending:
            self._refresh_pending = True
            self.after(160, self._flush_refresh)

    def _flush_refresh(self) -> None:
        self._refresh_pending = False
        self._refresh_table()
        enabled = [item for item in self.projects if item.enabled]
        if enabled:
            render_progress = sum(item.progress for item in enabled) / len(enabled)
            self._set_progress(45 + render_progress * 0.55)
            running = next((item for item in enabled if item.status in {"RUNNING", "QUEUED"}), None)
            if running:
                self.status_var.set(f"Đang render: {running.episode_id}")
                self.detail_var.set(running.current_message)

    def _worker_batch_finished(self, completed: int, failed: int) -> None:
        try:
            self.after(0, self._batch_finished, completed, failed)
        except tk.TclError:
            pass

    def _batch_finished(self, completed: int, failed: int) -> None:
        self.rendering = False
        self._set_running_controls(False)
        self._refresh_table()
        self._set_progress(100 if completed and not failed else self.progress_var.get())
        if failed:
            self.status_var.set("Tiến trình hoàn tất nhưng có lỗi")
            self.detail_var.set(f"Thành công: {completed} • Thất bại: {failed}. Có thể chọn lại nguồn để thử lại.")
        else:
            self.status_var.set("Hoàn thành tạo Video Recap")
            self.detail_var.set(f"Đã tạo thành công {completed} tập cùng hai file subtitle cho mỗi video.")
        output = self.batch.output_root if self.batch else None
        if self.settings.notify_complete or (failed and self.settings.notify_error):
            self.notifier.complete(completed, failed, output)
        messagebox.showinfo(
            "Kết quả Toolrecap",
            f"Hoàn thành: {completed}\nThất bại: {failed}\n\nThư mục kết quả:\n{output or 'Chưa có'}",
            parent=self,
        )

    def _refresh_table(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        if self.projects:
            for record in self.projects:
                stage = "Render"
                if record.status == "COMPLETED":
                    stage = "Hoàn thành"
                elif record.status == "FAILED":
                    stage = "Lỗi"
                tag = "completed" if record.status == "COMPLETED" else "failed" if record.status == "FAILED" else "running"
                self.tree.insert(
                    "",
                    "end",
                    iid=record.id,
                    values=(record.episode_id, Path(record.source_video).name, stage, f"{record.progress}%", record.current_message),
                    tags=(tag,),
                )
            return
        for index, video in enumerate(self.source_videos, start=1):
            match = re.search(r"(?i)(S\d{1,2}[ ._-]*E\d{1,3})", video.stem)
            episode = re.sub(r"[ ._-]+", "", match.group(1)).upper() if match else (video.stem if len(self.source_videos) == 1 else f"EP{index:03d}")
            stage = "Scanner" if self.analyzing else "Chờ"
            status = self.detail_var.get() if self.analyzing and index == 1 else "Sẵn sàng"
            self.tree.insert("", "end", iid=f"source-{index}", values=(episode, video.name, stage, "—", status))

    def _set_progress(self, value: float) -> None:
        value = max(0.0, min(100.0, float(value)))
        self.progress_var.set(value)
        self.progress_label_var.set(f"{round(value)}%")

    def _set_running_controls(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self.start_button.configure(state=state)
        self.file_button.configure(state=state)
        self.folder_button.configure(state=state)
        self.settings_button.configure(state=state)
        self.stop_button.configure(state="normal" if running else "disabled")

    def _stop(self) -> None:
        if self.analyzing:
            self.analysis_cancel.set()
        if self.rendering:
            self.queue.stop_all()
        self.status_var.set("Đang dừng an toàn…")
        self.detail_var.set("Các kết quả đã hoàn thành sẽ được giữ lại.")

    def _open_output(self) -> None:
        path = self.batch.output_root if self.batch else None
        if path is None and self.source is not None:
            base = self.source if self.source.is_dir() else self.source.parent
            path = base / self.settings.output_subdirectory
        if path is None:
            messagebox.showinfo("Kết quả", "Chưa có thư mục kết quả.", parent=self)
            return
        try:
            path.mkdir(parents=True, exist_ok=True)
            open_path(path)
        except Exception as exc:
            messagebox.showerror("Không thể mở thư mục kết quả", str(exc), parent=self)

    def _detect_gpu(self) -> None:
        def work() -> None:
            status = detect_gpu_encoder(refresh=True)
            self.after(0, setattr, self, "encoder_status", status)

        threading.Thread(target=work, daemon=True).start()

    def _busy(self) -> bool:
        return self.analyzing or self.rendering

    def _close(self) -> None:
        if self._busy() and not messagebox.askyesno(
            "Đóng Toolrecap",
            "Có tiến trình đang chạy. Dừng an toàn và đóng ứng dụng?",
            parent=self,
        ):
            return
        self.analysis_cancel.set()
        if hasattr(self, "queue"):
            self.queue.stop_all()
        AudioPreviewPlayer.stop()
        self.voice_manager.shutdown_all()
        self.destroy()


def run_auto_app() -> None:
    ToolrecapAutoApp().mainloop()

