from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .audio_preview import AudioPreviewPlayer
from .voice_system import LANGUAGE_NAMES, STYLE_NAMES, UnifiedTTSManager, VoiceRecord


class VoiceLibraryDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, manager: UnifiedTTSManager, selected_voice_id: str = "", selected_language: str | None = None) -> None:
        super().__init__(parent)
        self.title("Thư viện giọng")
        self.geometry("1180x720")
        self.minsize(920, 600)
        self.transient(parent)
        self.grab_set()
        self.manager = manager
        self.result: VoiceRecord | None = None
        self.selected_voice_id = selected_voice_id
        self.search_var = tk.StringVar()
        self.language_var = tk.StringVar(value=LANGUAGE_NAMES.get(selected_language or "", "Tất cả"))
        self.gender_var = tk.StringVar(value="Tất cả")
        self.style_var = tk.StringVar(value="Tất cả")
        self.engine_var = tk.StringVar(value="Tất cả")
        self.installed_var = tk.BooleanVar(value=True)
        self.favorite_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Sẵn sàng")
        self._voices: dict[str, VoiceRecord] = {}
        self._build()
        self._refresh()

    def _build(self) -> None:
        main = ttk.Frame(self, padding=14)
        main.pack(fill="both", expand=True)
        filters = ttk.LabelFrame(main, text="Tìm và lọc", padding=10)
        filters.pack(fill="x")
        ttk.Label(filters, text="Tìm kiếm:").grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(filters, textvariable=self.search_var, width=30)
        entry.grid(row=0, column=1, padx=(5, 18), sticky="ew")
        entry.bind("<KeyRelease>", lambda _event: self._refresh())
        ttk.Label(filters, text="Ngôn ngữ:").grid(row=0, column=2)
        language = ttk.Combobox(filters, textvariable=self.language_var, values=["Tất cả", *LANGUAGE_NAMES.values()], state="readonly", width=18)
        language.grid(row=0, column=3, padx=(5, 18))
        ttk.Label(filters, text="Giới tính:").grid(row=0, column=4)
        gender = ttk.Combobox(filters, textvariable=self.gender_var, values=["Tất cả", "Nam", "Nữ"], state="readonly", width=12)
        gender.grid(row=0, column=5, padx=(5, 18))
        ttk.Label(filters, text="Phong cách:").grid(row=1, column=0, sticky="w", pady=(9, 0))
        style = ttk.Combobox(filters, textvariable=self.style_var, values=["Tất cả", *STYLE_NAMES.values()], state="readonly", width=22)
        style.grid(row=1, column=1, padx=(5, 18), sticky="w", pady=(9, 0))
        ttk.Label(filters, text="Engine:").grid(row=1, column=2, pady=(9, 0))
        engines = sorted({item.engine for item in self.manager.catalog.load()})
        engine = ttk.Combobox(filters, textvariable=self.engine_var, values=["Tất cả", *engines], state="readonly", width=18)
        engine.grid(row=1, column=3, padx=(5, 18), pady=(9, 0))
        ttk.Checkbutton(filters, text="Chỉ đã cài", variable=self.installed_var, command=self._refresh).grid(row=1, column=4, sticky="w", pady=(9, 0))
        ttk.Checkbutton(filters, text="Chỉ yêu thích", variable=self.favorite_var, command=self._refresh).grid(row=1, column=5, sticky="w", pady=(9, 0))
        for widget in (language, gender, style, engine):
            widget.bind("<<ComboboxSelected>>", lambda _event: self._refresh())
        filters.columnconfigure(1, weight=1)

        frame = ttk.Frame(main)
        frame.pack(fill="both", expand=True, pady=10)
        columns = ("favorite", "name", "language", "gender", "styles", "engine", "native", "tier", "state", "license")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        headings = {"favorite":"★","name":"Tên giọng","language":"Ngôn ngữ","gender":"Giới tính","styles":"Phong cách","engine":"Engine","native":"Nguồn giọng","tier":"Chất lượng","state":"Trạng thái","license":"Giấy phép"}
        widths = {"favorite":38,"name":220,"language":125,"gender":75,"styles":210,"engine":100,"native":90,"tier":95,"state":90,"license":105}
        for key in columns:
            self.tree.heading(key, text=headings[key])
            self.tree.column(key, width=widths[key], anchor="w" if key in {"name","styles"} else "center")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _event: self._choose())
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._show_details())

        ttk.Label(main, textvariable=self.status_var, foreground="#075fc9", wraplength=880).pack(fill="x", pady=(0, 8))
        actions = ttk.Frame(main)
        actions.pack(fill="x")
        ttk.Button(actions, text="★ Yêu thích", command=self._toggle_favorite).pack(side="left")
        ttk.Button(actions, text="▶ Nghe thử", command=self._preview).pack(side="left", padx=6)
        ttk.Button(actions, text="■ Dừng nghe", command=AudioPreviewPlayer.stop).pack(side="left")
        ttk.Button(actions, text="Tải / sửa engine", command=self._install).pack(side="left", padx=(20, 0))
        ttk.Button(actions, text="Đóng", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="Dùng giọng này", command=self._choose).pack(side="right", padx=6)

    def _filtered(self) -> list[VoiceRecord]:
        voices = self.manager.catalog.load()
        favorites = self.manager.catalog.favorites()
        recent = self.manager.catalog.recent()
        search = self.search_var.get().strip().casefold()
        language = next((code for code, name in LANGUAGE_NAMES.items() if name == self.language_var.get()), None)
        gender = {"Nam":"male","Nữ":"female"}.get(self.gender_var.get())
        style = next((code for code, name in STYLE_NAMES.items() if name == self.style_var.get()), None)
        result = []
        for voice in voices:
            if search and search not in (voice.display_name + " " + voice.engine + " " + voice.description).casefold():
                continue
            if language and language not in voice.supported_languages:
                continue
            if gender and voice.gender != gender:
                continue
            if style and style not in voice.styles:
                continue
            if self.engine_var.get() != "Tất cả" and voice.engine != self.engine_var.get():
                continue
            if self.installed_var.get() and not voice.installed:
                continue
            if self.favorite_var.get() and voice.voice_id not in favorites:
                continue
            result.append(voice)
        rank = {voice_id: index for index, voice_id in enumerate(recent)}
        result.sort(key=lambda item: (item.voice_id not in favorites, not item.native_language, item.quality_tier != "premium_local", rank.get(item.voice_id, 999), item.display_name.casefold()))
        return result

    def _refresh(self) -> None:
        selected = self.tree.selection()[0] if self.tree.selection() else self.selected_voice_id
        selected_language = next((code for code, name in LANGUAGE_NAMES.items() if name == self.language_var.get()), None)
        self._voices.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        favorites = self.manager.catalog.favorites()
        for voice in self._filtered():
            self._voices[voice.voice_id] = voice
            styles = ", ".join(STYLE_NAMES.get(item, item) for item in voice.styles[:3])
            native_for_selection = voice.native_language and (selected_language is None or voice.language == selected_language)
            self.tree.insert("", "end", iid=voice.voice_id, values=("★" if voice.voice_id in favorites else "☆", voice.display_name, LANGUAGE_NAMES.get(voice.language, voice.language), {"male":"Nam","female":"Nữ","neutral":"Trung tính"}.get(voice.gender, voice.gender), styles, voice.engine, "Bản địa" if native_for_selection else "Đa ngôn ngữ", "Premium" if voice.quality_tier == "premium_local" else "Standard", "Đã cài" if voice.installed else "Chưa cài", voice.license))
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)
            self.tree.see(selected)
        self.status_var.set(f"{len(self._voices)} giọng phù hợp." if self._voices else "Không có giọng phù hợp. Hãy đổi bộ lọc hoặc từ tìm kiếm.")

    def _show_details(self) -> None:
        voice = self._selected()
        if voice:
            scope = "Chỉ dùng cá nhân" if voice.personal_use_only else "Xem giấy phép nguồn"
            self.status_var.set(f"{voice.description} | {scope} | {voice.source}")

    def _selected(self) -> VoiceRecord | None:
        selected = self.tree.selection()
        return self._voices.get(selected[0]) if selected else None

    def _toggle_favorite(self) -> None:
        voice = self._selected()
        if voice:
            favorites = self.manager.catalog.favorites()
            self.manager.catalog.set_favorite(voice.voice_id, voice.voice_id not in favorites)
            self._refresh()

    def _preview(self) -> None:
        voice = self._selected()
        if not voice:
            return
        if not voice.installed:
            self.status_var.set("Engine chưa cài. Bấm Tải / sửa engine.")
            return
        self.status_var.set("Đang chuẩn bị giọng nghe thử…")

        def worker() -> None:
            try:
                path = self.manager.preview(voice.voice_id, voice.language, "film_recap")
                self.after(0, lambda: AudioPreviewPlayer.play(path))
                self.after(0, lambda: self.status_var.set("Đang phát: " + self.manager.preview_text(voice.language)))
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda: messagebox.showerror("Không thể nghe thử", message, parent=self))
                self.after(0, lambda: self.status_var.set("Giọng này đang bị lỗi."))

        threading.Thread(target=worker, daemon=True).start()

    def _install(self) -> None:
        voice = self._selected()
        if not voice:
            return
        self.status_var.set(f"Đang tải/kiểm tra engine {voice.engine}. Quá trình này có thể mất vài phút…")

        def worker() -> None:
            health = self.manager.install_engine(voice.engine)
            self.after(0, lambda: self.status_var.set(health.message))
            self.after(0, self._refresh)

        threading.Thread(target=worker, daemon=True).start()

    def _choose(self) -> None:
        voice = self._selected()
        if not voice:
            return
        if not voice.installed:
            self.status_var.set("Hãy tải engine trước khi chọn giọng này.")
            return
        self.manager.catalog.record_recent(voice.voice_id)
        self.result = voice
        self.destroy()
