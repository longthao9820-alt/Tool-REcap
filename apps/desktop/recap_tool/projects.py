from __future__ import annotations

import json
import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .batch import ImportedEpisode
from .credentials import default_data_directory
from .renderer import RecapRenderer, RenderSettings
from .settings import SettingsStore


ProjectCallback = Callable[["ProjectRecord"], None]
BatchCallback = Callable[[int, int], None]


@dataclass
class StageStates:
    cut: str = "WAITING"
    voice: str = "WAITING"
    render: str = "WAITING"


@dataclass
class ProjectRecord:
    id: str
    name: str
    episode_id: str
    source_video: str
    manifest_path: str
    output_directory: str
    recap_mode: str
    recap_language: str
    output_count: int
    available_recap_modes: list[str] = field(default_factory=list)
    output_counts_by_mode: dict[str, int] = field(default_factory=dict)
    voice_engine: str = ""
    voice_id: str = ""
    voice_style: str = "film_recap"
    enabled: bool = True
    generate_srt: bool = True
    burn_subtitles: bool = True
    quality: str = "high"
    use_gpu: bool = True
    encoder: str = ""
    status: str = "WAITING"
    progress: int = 0
    current_message: str = "Sẵn sàng"
    error: str | None = None
    stages: StageStates = field(default_factory=StageStates)
    output_files: list[str] = field(default_factory=list)

    @classmethod
    def from_episode(
        cls,
        episode: ImportedEpisode,
        *,
        output_root: Path,
        voice_engine: str,
        voice_id: str,
        voice_style: str = "film_recap",
        generate_srt: bool,
        burn_subtitles: bool,
        quality: str,
        use_gpu: bool,
        encoder: str,
    ) -> "ProjectRecord":
        return cls(
            id=f"{episode.episode_id}-{uuid.uuid4().hex[:8]}",
            name=episode.title,
            episode_id=episode.episode_id,
            source_video=str(episode.source_path or ""),
            manifest_path=str(episode.manifest_path),
            output_directory=str(output_root / episode.episode_id),
            recap_mode=episode.recap_mode,
            recap_language=episode.recap_language,
            output_count=episode.output_count,
            available_recap_modes=list(episode.available_recap_modes or (episode.recap_mode,)),
            output_counts_by_mode=dict(episode.output_counts_by_mode or {episode.recap_mode: episode.output_count}),
            voice_engine=voice_engine,
            voice_id=voice_id,
            voice_style=voice_style,
            enabled=episode.source_path is not None,
            generate_srt=generate_srt,
            burn_subtitles=burn_subtitles,
            quality=quality,
            use_gpu=use_gpu,
            encoder=encoder,
            current_message="Sẵn sàng" if episode.source_path else "Không tìm thấy video nguồn",
        )


class ProjectStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_data_directory() / "projects.json"
        self._lock = threading.RLock()

    def load(self) -> list[ProjectRecord]:
        with self._lock:
            if not self.path.is_file():
                return []
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                from .voice_system import UnifiedTTSManager

                manager = UnifiedTTSManager()
                records: list[ProjectRecord] = []
                allowed = ProjectRecord.__dataclass_fields__
                for raw in payload.get("projects", []):
                    if not isinstance(raw, dict) or not raw.get("manifest_path"):
                        continue
                    values = {key: value for key, value in raw.items() if key in allowed and key != "stages"}
                    values.setdefault("episode_id", str(values.get("name") or "episode"))
                    values.setdefault("recap_mode", "MAIN_STORIES")
                    values.setdefault("recap_language", "en-US")
                    values.setdefault("output_count", 0)
                    values.setdefault("available_recap_modes", [values["recap_mode"]])
                    values.setdefault("output_counts_by_mode", {values["recap_mode"]: values["output_count"]})
                    values.setdefault("source_video", "")
                    values.setdefault("output_directory", "")
                    values.setdefault("id", uuid.uuid4().hex[:12])
                    stage_raw = raw.get("stages") if isinstance(raw.get("stages"), dict) else {}
                    record = ProjectRecord(**values, stages=StageStates(**{key: value for key, value in stage_raw.items() if key in StageStates.__dataclass_fields__}))
                    migrated = manager.migrate_voice_id(record.voice_id)
                    if migrated != record.voice_id:
                        record.voice_id = migrated
                        record.voice_engine = migrated.split(".", 1)[0]
                    if record.status in {"RUNNING", "QUEUED"}:
                        record.status = "PAUSED"
                        record.current_message = "Đã dừng khi ứng dụng đóng trước đó"
                    records.append(record)
                return records
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                return []

    def replace(self, projects: list[ProjectRecord]) -> None:
        self.save(projects)

    def save(self, projects: list[ProjectRecord]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".partial")
            temporary.write_text(json.dumps({"projects": [asdict(item) for item in projects]}, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)


class ProjectQueue:
    def __init__(
        self,
        projects: list[ProjectRecord],
        *,
        store: ProjectStore,
        settings_store: SettingsStore,
        callback: ProjectCallback | None = None,
        batch_callback: BatchCallback | None = None,
    ) -> None:
        self.projects = projects
        self.store = store
        self.settings_store = settings_store
        self.callback = callback or (lambda _record: None)
        self.batch_callback = batch_callback or (lambda _completed, _failed: None)
        self._lock = threading.RLock()
        self._executor: ThreadPoolExecutor | None = None
        self._futures: dict[str, Future] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._active_batch: set[str] = set()
        self._last_notify_at: dict[str, float] = {}

    def replace_projects(self, projects: list[ProjectRecord]) -> None:
        self.stop_all()
        with self._lock:
            self.projects[:] = projects
            self.store.replace(self.projects)

    def run(self, project_ids: list[str]) -> None:
        settings = self.settings_store.load()
        selected = [project_id for project_id in project_ids if (record := self._find(project_id)) and record.enabled]
        if not selected:
            return
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=max(1, min(4, settings.max_concurrent_projects)), thread_name_prefix="recap-render")
            self._active_batch.update(selected)
            for project_id in selected:
                if project_id in self._futures and not self._futures[project_id].done():
                    continue
                record = self._find(project_id)
                if not record:
                    continue
                event = threading.Event()
                self._cancel[project_id] = event
                record.error = None
                record.status = "QUEUED"
                record.current_message = "Đã thêm vào hàng đợi"
                self._persist_notify(record, force=True)
                future = self._executor.submit(self._run_one, record, event)
                future.add_done_callback(lambda _future, pid=project_id: self._finished(pid))
                self._futures[project_id] = future

    def run_enabled(self) -> None:
        self.run([item.id for item in self.projects if item.enabled and item.status not in {"RUNNING", "QUEUED"}])

    def stop(self, project_id: str) -> None:
        event = self._cancel.get(project_id)
        if event:
            event.set()

    def stop_all(self) -> None:
        for event in list(self._cancel.values()):
            event.set()

    def _find(self, project_id: str) -> ProjectRecord | None:
        return next((item for item in self.projects if item.id == project_id), None)

    def _persist_notify(self, record: ProjectRecord, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_notify_at.get(record.id, 0.0) < 0.2:
            return
        with self._lock:
            self.store.save(self.projects)
        self._last_notify_at[record.id] = now
        self.callback(record)

    def _run_one(self, record: ProjectRecord, cancel_event: threading.Event) -> None:
        try:
            if not Path(record.source_video).is_file():
                raise RuntimeError("Không tìm thấy video nguồn.")
            if not record.voice_engine or not record.voice_id:
                raise RuntimeError("Chưa chọn giọng đọc.")
            record.status = "RUNNING"
            record.progress = 1
            record.current_message = "Đang chuẩn bị render"
            record.stages.cut = "RUNNING"
            self._persist_notify(record, force=True)
            renderer = RecapRenderer(
                progress=lambda current, total, message: self._render_progress(record, current, total, message),
                log=lambda message: self._log(record, message),
                cancel_event=cancel_event,
            )
            results = renderer.render(
                RenderSettings(
                    manifest_path=Path(record.manifest_path),
                    source_override=Path(record.source_video),
                    output_directory=Path(record.output_directory),
                    voice_engine=record.voice_engine,
                    voice_id=record.voice_id,
                    voice_style=record.voice_style,
                    generate_srt=record.generate_srt,
                    burn_subtitles=record.burn_subtitles,
                    quality=record.quality,
                    use_gpu=record.use_gpu,
                    recap_mode=record.recap_mode,
                )
            )
            record.stages.cut = "DONE"
            record.stages.voice = "DONE"
            record.stages.render = "DONE"
            record.output_files = [str(item.video_path) for item in results]
            record.progress = 100
            record.status = "COMPLETED"
            record.current_message = f"Hoàn thành {len(results)} video"
            self._persist_notify(record, force=True)
        except Exception as exc:
            if cancel_event.is_set():
                record.status = "PAUSED"
                record.current_message = "Đã dừng"
            else:
                record.status = "FAILED"
                record.error = str(exc)
                record.current_message = " ".join(str(exc).split())[:180]
            self._persist_notify(record, force=True)

    def _render_progress(self, record: ProjectRecord, current: int, total: int, message: str) -> None:
        ratio = current / max(total, 1)
        record.progress = max(1, min(99, int(ratio * 100)))
        record.current_message = message
        if ratio < 0.4:
            record.stages.cut = "RUNNING"
        elif ratio < 0.75:
            record.stages.cut = "DONE"
            record.stages.voice = "RUNNING"
        else:
            record.stages.cut = "DONE"
            record.stages.voice = "DONE"
            record.stages.render = "RUNNING"
        self._persist_notify(record)

    def _finished(self, project_id: str) -> None:
        with self._lock:
            self._active_batch.discard(project_id)
            if self._active_batch:
                return
            completed = sum(item.status == "COMPLETED" for item in self.projects if item.enabled)
            failed = sum(item.status == "FAILED" for item in self.projects if item.enabled)
        self.batch_callback(completed, failed)

    @staticmethod
    def _log(record: ProjectRecord, message: str) -> None:
        log_dir = Path(record.output_directory).parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / f"{record.episode_id}.log").open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")
