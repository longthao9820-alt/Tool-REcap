from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .media import (
    MediaError,
    burn_subtitles,
    calculate_video_fit,
    calculate_video_padding,
    calculate_video_speed,
    concat_videos,
    cut_clip,
    probe_duration,
    probe_media,
    render_narration_segment,
)
from .gpu import video_encode_args
from .credentials import default_data_directory
from .models import ProjectData, load_project, ordered_clips, ordered_outputs, ordered_segments
from .originality import audit_project
from .speech_to_text import transcribe_video_audio
from .tts import LocalSpeechClient
from .voice_system import UnifiedTTSManager, VoiceSystemError, validate_voice_audio


ProgressCallback = Callable[[int, int, str], None]
LogCallback = Callable[[str], None]


def filesystem_safe_component(value: str, max_length: int = 36) -> str:
    """Tên ngắn, ổn định cho file trung gian trên Windows."""
    cleaned = "".join(character if character.isalnum() or character in "._-" else "-" for character in value)
    cleaned = cleaned.strip(".-_") or "item"
    if len(cleaned) <= max_length:
        return cleaned
    digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:10]
    prefix_length = max(1, max_length - len(digest) - 1)
    return f"{cleaned[:prefix_length].rstrip('.-_')}-{digest}"


def bounded_output_path(directory: Path, stem: str, suffix: str, max_total_length: int = 235) -> Path:
    """Giữ tên dễ đọc nhưng không để đường dẫn output vượt ngưỡng an toàn."""
    resolved_directory = directory.resolve()
    available = max(20, max_total_length - len(str(resolved_directory)) - len(suffix) - 1)
    return resolved_directory / f"{filesystem_safe_component(stem, available)}{suffix}"


def publication_title(value: object, directory: Path, *, max_total_length: int = 235) -> str:
    """Validate a human-readable title that can be the exact Windows filename."""
    title = " ".join(str(value or "").split()).strip(". ")
    if not title:
        raise MediaError("Video đầu ra thiếu title để đặt tên file.")
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', title):
        raise MediaError(f"Title chứa ký tự không hợp lệ cho tên file Windows: {title}")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if title.upper() in reserved:
        raise MediaError(f"Title trùng tên dành riêng của Windows: {title}")
    longest_suffix = ".narration.srt"
    if len(str(directory.resolve() / f"{title}{longest_suffix}")) > max_total_length:
        raise MediaError("Title quá dài để dùng nguyên văn làm tên file. Hãy rút ngắn title trong JSON.")
    return title


def split_subtitle_phrases(text: str, *, minimum_words: int = 3, maximum_words: int = 7) -> list[str]:
    """Split narration into short, import-friendly caption phrases."""
    tokens = text.split()
    if not tokens:
        return []
    groups: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        current.append(token)
        boundary = bool(re.search(r"[,;:.!?][\"')\]]*$", token))
        if len(current) >= maximum_words or (boundary and len(current) >= minimum_words):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    if len(groups) >= 2 and len(groups[-1]) < minimum_words:
        needed = minimum_words - len(groups[-1])
        movable = max(0, len(groups[-2]) - minimum_words)
        take = min(needed, movable)
        if take:
            groups[-1] = groups[-2][-take:] + groups[-1]
            groups[-2] = groups[-2][:-take]
        if len(groups[-1]) < minimum_words and len(groups[-2]) + len(groups[-1]) <= maximum_words:
            groups[-2].extend(groups.pop())
    return [" ".join(group).strip() for group in groups if group]


def timed_subtitle_rows(text: str, start: float, end: float) -> list[tuple[float, float, str]]:
    phrases = split_subtitle_phrases(text)
    if not phrases or end <= start:
        return []
    weights = []
    for phrase in phrases:
        word_count = max(1, len(re.findall(r"\b[\w']+\b", phrase, flags=re.UNICODE)))
        pause_weight = 0.65 if re.search(r"[.!?][\"')\]]*$", phrase) else 0.25 if re.search(r"[,;:][\"')\]]*$", phrase) else 0.0
        weights.append(word_count + pause_weight)
    total_weight = sum(weights)
    duration = end - start
    rows: list[tuple[float, float, str]] = []
    cursor = start
    for index, (phrase, weight) in enumerate(zip(phrases, weights)):
        phrase_end = end if index == len(phrases) - 1 else cursor + duration * weight / total_weight
        rows.append((cursor, phrase_end, phrase))
        cursor = phrase_end
    return rows


@dataclass(frozen=True)
class RenderSettings:
    manifest_path: Path
    source_override: Path | None
    output_directory: Path
    voice_engine: str
    voice_id: str
    voice_style: str = "film_recap"
    generate_srt: bool = True
    burn_subtitles: bool = False
    quality: str = "high"
    use_gpu: bool = True
    recap_mode: str | None = None


@dataclass(frozen=True)
class RenderedOutput:
    render_id: str
    output_type: str
    video_path: Path
    narration_subtitle_path: Path
    original_subtitle_path: Path
    duration_seconds: float

    @property
    def subtitle_path(self) -> Path:
        """Compatibility alias for callers that previously expected one SRT."""
        return self.narration_subtitle_path


class RecapRenderer:
    def __init__(
        self,
        *,
        progress: ProgressCallback | None = None,
        log: LogCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.progress = progress or (lambda _current, _total, _message: None)
        self.log = log or (lambda _message: None)
        self.cancel_event = cancel_event or threading.Event()

    def render(self, settings: RenderSettings) -> list[RenderedOutput]:
        project = load_project(settings.manifest_path)
        source = self._resolve_source(project, settings.source_override)
        source_info = probe_media(source)
        self._validate_clip_bounds(project, source_info["duration"])

        settings.output_directory.mkdir(parents=True, exist_ok=True)
        work_identity = "|".join(
            (
                str(settings.manifest_path.resolve()),
                str(settings.output_directory.resolve()),
                project.project_id,
            )
        )
        work_key = hashlib.sha256(work_identity.encode("utf-8", "replace")).hexdigest()[:20]
        work_root = default_data_directory() / "render-work" / work_key
        work_root.mkdir(parents=True, exist_ok=True)
        cache_root = default_data_directory() / "render-cache" / "voices"
        cache_root.mkdir(parents=True, exist_ok=True)

        outputs = outputs_for_recap_mode(project, settings.recap_mode)
        publication_titles = [publication_title(output.get("title"), settings.output_directory) for output in outputs]
        if len({title.casefold() for title in publication_titles}) != len(publication_titles):
            raise MediaError("Các output phải có title khác nhau để tạo tên file không trùng.")
        total_segments = sum(len(item["segments"]) for item in outputs)
        total_units = max(1, total_segments + len(outputs))
        completed_units = 0
        client = LocalSpeechClient()
        results: list[RenderedOutput] = []
        source_transcript: list[dict] = []
        needs_source_transcript = any(
            segment.get("segment_type") == "original_dialogue" and not self._original_dialogue_text(segment)
            for output in outputs
            for segment in ordered_segments(output)
        )
        if needs_source_transcript:
            source_stat = source.stat()
            source_identity = f"{source.resolve()}|{source_stat.st_size}|{source_stat.st_mtime_ns}"
            transcript_key = hashlib.sha256(source_identity.encode("utf-8", "replace")).hexdigest()[:20]
            # Reuse the transcript created by the AI-analysis stage whenever it
            # exists; otherwise create it once in the same source-bound cache.
            transcript_cache = default_data_directory() / "api-analysis" / transcript_key
            embedded_subtitle_cache = transcript_cache / "subtitles.srt"
            if embedded_subtitle_cache.is_file():
                from .recap_api import _parse_subtitles

                source_transcript = _parse_subtitles(embedded_subtitle_cache)
                self.log("JSON cũ chưa có original_dialogue_text; dùng subtitle nguồn đã lưu để tạo .original.srt.")
            else:
                self.log("JSON cũ chưa có original_dialogue_text; đang đọc lời thoại nguồn để tạo subtitle gốc…")
                source_transcript = transcribe_video_audio(
                    source,
                    transcript_cache,
                    self.cancel_event,
                    self.log,
                )

        self.log(f"Dự án: {project.project_id}")
        self.log(f"Video nguồn: {source}")
        self.log(f"Kiểu recap: {settings.recap_mode or project.raw.get('recap_mode') or 'theo JSON'}")
        self.log(f"Giọng: {settings.voice_engine} / {settings.voice_id}")

        for output, title in zip(outputs, publication_titles):
            self._check_cancelled()
            render_id = str(output["render_id"])
            output_token = filesystem_safe_component(render_id)
            output_work = work_root / output_token
            clips_dir = output_work / "clips"
            voices_dir = output_work / "voices"
            segments_dir = output_work / "segments"
            for directory in (clips_dir, voices_dir, segments_dir):
                directory.mkdir(parents=True, exist_ok=True)

            rendered_segments: list[Path] = []
            narration_subtitles: list[tuple[float, float, str]] = []
            original_subtitles: list[tuple[float, float, str]] = []
            timeline_cursor = 0.0

            for segment in ordered_segments(output):
                self._check_cancelled()
                segment_id = str(segment["segment_id"])
                segment_token = filesystem_safe_component(segment_id)
                self.log(f"Đang xử lý {render_id} / {segment_id}")
                clips = ordered_clips(segment)
                narration = ""
                voice_path: Path | None = None
                voice_duration = 0.0
                retain_ratio = 1.0

                if segment["segment_type"] != "original_dialogue":
                    narration = str(segment["narration_text"]).strip()
                    voice_path = voices_dir / f"{segment_token}.wav"
                    cached_voice = cache_root / f"{client.manager.cache_key(narration, settings.voice_id, project.recap_language, settings.voice_style)}.wav"
                    cache_is_valid = False
                    if cached_voice.is_file() and cached_voice.stat().st_size > 0:
                        try:
                            validate_voice_audio(cached_voice)
                        except VoiceSystemError:
                            cached_voice.unlink(missing_ok=True)
                            self.log(f"{segment_id}: cache giọng không hợp lệ, đang tạo lại")
                        else:
                            cache_is_valid = True
                    if cache_is_valid:
                        shutil.copy2(cached_voice, voice_path)
                    else:
                        client.synthesize(
                            text=narration,
                            output_path=voice_path,
                            engine=settings.voice_engine,
                            voice_id=settings.voice_id,
                            language=project.recap_language,
                            style=settings.voice_style,
                        )
                        shutil.copy2(voice_path, cached_voice)
                    voice_duration = probe_duration(voice_path)
                    selected_duration = sum(
                        (float(clip["end_ms"]) - float(clip["start_ms"])) / 1000.0
                        for clip in clips
                    )
                    policy = project.raw["render_policy"]
                    planned_speed, retain_ratio = calculate_video_fit(
                        selected_duration,
                        voice_duration,
                        float(policy["video_speed_absolute_min"]),
                        float(policy["video_speed_absolute_max"]),
                    )
                    padding_duration = calculate_video_padding(
                        selected_duration,
                        voice_duration,
                        planned_speed,
                    )
                    if retain_ratio < 0.9999:
                        removed_duration = selected_duration * (1.0 - retain_ratio)
                        self.log(
                            f"{segment_id}: hình dài hơn giọng, sẽ cắt tổng {removed_duration:.2f}s "
                            f"theo tỷ lệ trên {len(clips)} clip để không bỏ mất cảnh"
                        )
                    elif padding_duration > 0.02:
                        self.log(
                            f"{segment_id}: giọng dài hơn hình {padding_duration:.2f}s sau khi làm chậm "
                            f"đến {planned_speed:.3f}x; tool sẽ tự giữ frame cuối để hoàn tất lời đọc"
                        )

                segment_clips: list[Path] = []
                for clip in clips:
                    start_ms = float(clip["start_ms"])
                    end_ms = start_ms + (float(clip["end_ms"]) - start_ms) * retain_ratio
                    fit_suffix = "" if retain_ratio >= 0.9999 else f".fit-{int(round(end_ms - start_ms))}ms"
                    clip_token = filesystem_safe_component(str(clip["clip_id"]))
                    clip_path = clips_dir / f"{clip_token}{fit_suffix}.mp4"
                    if not clip_path.is_file() or clip_path.stat().st_size == 0:
                        cut_clip(
                            source,
                            start_ms,
                            end_ms,
                            clip_path,
                            quality=settings.quality,
                            use_gpu=settings.use_gpu,
                            cancel_event=self.cancel_event,
                            log=self.log,
                        )
                    segment_clips.append(clip_path)

                combined_clip = segments_dir / f"{segment_token}.source.mp4"
                if len(segment_clips) == 1:
                    shutil.copy2(segment_clips[0], combined_clip)
                else:
                    concat_videos(
                        segment_clips,
                        combined_clip,
                        cancel_event=self.cancel_event,
                        log=self.log,
                    )

                final_segment = segments_dir / f"{segment_token}.final.mp4"
                if segment["segment_type"] == "original_dialogue":
                    shutil.copy2(combined_clip, final_segment)
                    segment_duration = probe_duration(final_segment)
                    original_subtitles.extend(
                        self._original_subtitle_rows(
                            segment,
                            clips,
                            source_transcript,
                            timeline_cursor,
                            segment_duration,
                        )
                    )
                else:
                    assert voice_path is not None
                    video_duration = probe_duration(combined_clip)
                    policy = project.raw["render_policy"]
                    speed = calculate_video_speed(
                        video_duration,
                        voice_duration,
                        float(policy["video_speed_absolute_min"]),
                        float(policy["video_speed_absolute_max"]),
                    )
                    self.log(
                        f"{segment_id}: hình {video_duration:.2f}s, giọng {voice_duration:.2f}s, tốc độ hình {speed:.3f}x"
                    )
                    render_narration_segment(
                        combined_clip,
                        voice_path,
                        final_segment,
                        speed=speed,
                        quality=settings.quality,
                        use_gpu=settings.use_gpu,
                        cancel_event=self.cancel_event,
                        log=self.log,
                    )
                    segment_duration = probe_duration(final_segment)
                    if segment.get("subtitle", True):
                        narration_subtitles.extend(
                            timed_subtitle_rows(
                                narration,
                                timeline_cursor,
                                timeline_cursor + segment_duration,
                            )
                        )

                timeline_cursor += segment_duration
                rendered_segments.append(final_segment)
                completed_units += 1
                self.progress(completed_units, total_units, f"Đã xử lý {segment_id}")

            base_video = output_work / f"{output_token}.base.mp4"
            concat_videos(
                rendered_segments,
                base_video,
                cancel_event=self.cancel_event,
                log=self.log,
            )

            temporary_narration_srt = output_work / f"{output_token}.narration.srt"
            temporary_original_srt = output_work / f"{output_token}.original.srt"
            temporary_combined_srt = output_work / f"{output_token}.combined.srt"
            self._write_srt(temporary_narration_srt, narration_subtitles)
            self._write_srt(temporary_original_srt, original_subtitles)
            combined_subtitles = sorted([*narration_subtitles, *original_subtitles], key=lambda row: (row[0], row[1]))
            self._write_srt(temporary_combined_srt, combined_subtitles)

            final_video = settings.output_directory.resolve() / f"{title}.mp4"
            narration_subtitle_path = settings.output_directory.resolve() / f"{title}.narration.srt"
            original_subtitle_path = settings.output_directory.resolve() / f"{title}.original.srt"
            if settings.burn_subtitles and combined_subtitles:
                burn_subtitles(
                    base_video,
                    temporary_combined_srt,
                    final_video,
                    quality=settings.quality,
                    use_gpu=settings.use_gpu,
                    cancel_event=self.cancel_event,
                    log=self.log,
                )
            else:
                shutil.copy2(base_video, final_video)
            shutil.copy2(temporary_narration_srt, narration_subtitle_path)
            shutil.copy2(temporary_original_srt, original_subtitle_path)

            duration = probe_duration(final_video)
            final_info = probe_media(final_video)
            if final_info["width"] != source_info["width"] or final_info["height"] != source_info["height"]:
                raise MediaError(
                    "Video đầu ra không giữ đúng kích thước và tỷ lệ của video nguồn. Tool đã dừng để tránh xuất sai."
                )
            if not final_info["has_audio"]:
                raise MediaError("Video đầu ra không có âm thanh. Tool đã dừng để tránh xuất file lỗi.")

            result = RenderedOutput(
                render_id=render_id,
                output_type=str(output["type"]),
                video_path=final_video,
                narration_subtitle_path=narration_subtitle_path,
                original_subtitle_path=original_subtitle_path,
                duration_seconds=duration,
            )
            results.append(result)
            completed_units += 1
            self.progress(completed_units, total_units, f"Đã tạo {final_video.name}")
            self.log(f"Hoàn thành: {final_video}")

        self._write_report(settings.output_directory, project, source_info, settings, results)
        return results

    def _resolve_source(self, project: ProjectData, override: Path | None) -> Path:
        source = override.expanduser().resolve() if override else project.default_source_path()
        if not source.is_file():
            raise MediaError(f"Không tìm thấy video nguồn: {source}")
        return source

    def _validate_clip_bounds(self, project: ProjectData, source_duration: float) -> None:
        maximum_ms = source_duration * 1000.0 + 250.0
        for output in ordered_outputs(project):
            for segment in ordered_segments(output):
                for clip in ordered_clips(segment):
                    if float(clip["end_ms"]) > maximum_ms:
                        raise MediaError(
                            f"Clip {clip['clip_id']} kết thúc ngoài thời lượng video nguồn."
                        )

    @staticmethod
    def _original_dialogue_text(segment: dict) -> str:
        for key in ("original_dialogue_text", "dialogue_text", "subtitle_text", "source_text"):
            value = segment.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _original_subtitle_rows(
        self,
        segment: dict,
        clips: list[dict],
        source_transcript: list[dict],
        timeline_start: float,
        segment_duration: float,
    ) -> list[tuple[float, float, str]]:
        supplied_text = self._original_dialogue_text(segment)
        if supplied_text:
            return timed_subtitle_rows(supplied_text, timeline_start, timeline_start + segment_duration)

        rows: list[tuple[float, float, str]] = []
        clip_offset = 0.0
        for clip in clips:
            clip_start = float(clip["start_ms"]) / 1000.0
            clip_end = float(clip["end_ms"]) / 1000.0
            clip_duration = max(0.0, clip_end - clip_start)
            for item in source_transcript:
                try:
                    item_start = float(item["start"])
                    item_end = float(item["end"])
                except (KeyError, TypeError, ValueError):
                    continue
                midpoint = (item_start + item_end) / 2.0
                text = str(item.get("text") or "").strip()
                if not text or midpoint < clip_start or midpoint > clip_end:
                    continue
                overlap_start = max(clip_start, item_start)
                overlap_end = min(clip_end, item_end)
                if overlap_end <= overlap_start:
                    continue
                final_start = timeline_start + clip_offset + (overlap_start - clip_start)
                final_end = timeline_start + clip_offset + (overlap_end - clip_start)
                rows.extend(timed_subtitle_rows(text, final_start, final_end))
            clip_offset += clip_duration
        if not rows:
            self.log(
                f"{segment.get('segment_id', 'original-dialogue')}: không nhận diện được lời thoại gốc; "
                "file .original.srt vẫn được tạo rỗng để người dùng kiểm tra thủ công."
            )
        return rows

    def _check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise MediaError("Người dùng đã dừng xử lý.")

    @staticmethod
    def _voice_cache_key(text: str, settings: RenderSettings, language: str) -> str:
        payload = "\n".join((text, settings.voice_engine, settings.voice_id, language, settings.voice_style, "speed=1.0"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _srt_time(seconds: float) -> str:
        milliseconds = max(0, int(round(seconds * 1000)))
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, millis = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    @classmethod
    def _write_srt(cls, path: Path, rows: list[tuple[float, float, str]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        blocks = []
        for index, (start, end, text) in enumerate(rows, start=1):
            blocks.append(
                f"{index}\n{cls._srt_time(start)} --> {cls._srt_time(end)}\n{text.strip()}\n"
            )
        path.write_text("\n".join(blocks), encoding="utf-8-sig")

    @staticmethod
    def _write_report(
        directory: Path,
        project: ProjectData,
        source_info: dict,
        settings: RenderSettings,
        results: list[RenderedOutput],
    ) -> None:
        voice_metadata = UnifiedTTSManager().voice(settings.voice_id)
        report = {
            "project_id": project.project_id,
            "recap_mode": settings.recap_mode or project.raw.get("recap_mode"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "source": source_info,
            "source_rights": project.raw.get("source_rights", {"status": "UNVERIFIED"}),
            "originality": audit_project(project, recap_mode=settings.recap_mode).to_dict(),
            "voice": {
                "runtime": "local",
                "engine": settings.voice_engine,
                "voice_id": settings.voice_id,
                "style": settings.voice_style,
                "speed": 1.0,
                "license": voice_metadata.license,
                "source": voice_metadata.source,
                "commercial_use": voice_metadata.commercial_use,
                "attribution_required": voice_metadata.attribution_required,
                "model_version": voice_metadata.model_version,
            },
            "video_encoder": video_encode_args(settings.quality, use_gpu=settings.use_gpu)[1].encoder,
            "outputs": [
                {
                    "render_id": item.render_id,
                    "type": item.output_type,
                    "video": str(item.video_path),
                    "narration_subtitle": str(item.narration_subtitle_path),
                    "original_subtitle": str(item.original_subtitle_path),
                    "duration_seconds": round(item.duration_seconds, 3),
                }
                for item in results
            ],
        }
        del directory
        report_directory = default_data_directory() / "render-reports"
        report_directory.mkdir(parents=True, exist_ok=True)
        path = bounded_output_path(report_directory, f"{project.project_id}-bao-cao-render", ".json")
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def outputs_for_recap_mode(project: ProjectData, recap_mode: str | None) -> list[dict]:
    outputs = ordered_outputs(project)
    mode = str(recap_mode or project.raw.get("recap_mode") or "").upper()
    if mode == "FULL_EPISODE":
        selected = [item for item in outputs if item.get("type") == "FULL_RECAP"]
    elif mode == "MAIN_STORIES":
        selected = [item for item in outputs if item.get("type") == "MAIN_STORY"]
    else:
        selected = outputs
    if not selected:
        raise MediaError(f"JSON không có output cho kiểu recap {mode or 'đã chọn'}.")
    if mode == "FULL_EPISODE" and len(selected) != 1:
        raise MediaError("Chế độ recap cả tập phải có đúng một output FULL_RECAP.")
    return selected
