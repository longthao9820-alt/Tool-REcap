from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .credentials import default_data_directory
from .models import ProjectValidationError, load_project
from .edit_plan import SmoothEditPlanError, validate_smooth_edit_plan


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts"}
SUPPORTED_LANGUAGES = {"en-US", "en-GB"}

# Dùng nhịp bảo thủ để kế hoạch hình không bị ngắn hơn TTS thật. Tiếng Đức cần
# thêm dư địa cho từ ghép và dấu câu; hình dư có thể được renderer cắt an toàn.
_PLANNING_WORDS_PER_MINUTE = {
    "en-US": 145,
    "en-GB": 145,
}


@dataclass(frozen=True)
class ImportedEpisode:
    episode_id: str
    title: str
    source_file: str
    source_path: Path | None
    manifest_path: Path
    recap_mode: str
    recap_language: str
    output_count: int
    available_recap_modes: tuple[str, ...] = ()
    output_counts_by_mode: dict[str, int] | None = None


@dataclass(frozen=True)
class BatchImport:
    json_path: Path
    project_name: str
    media_root: Path
    output_root: Path
    episodes: tuple[ImportedEpisode, ...]

    @property
    def found_count(self) -> int:
        return sum(item.source_path is not None for item in self.episodes)


def import_recap_json(path: str | Path, media_root: str | Path | None = None) -> BatchImport:
    json_path = Path(path).expanduser().resolve()
    if not json_path.is_file():
        raise ProjectValidationError(f"Không tìm thấy file JSON: {json_path}")
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ProjectValidationError(
            f"JSON không hợp lệ tại dòng {exc.lineno}, cột {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(raw, dict):
        raise ProjectValidationError("Nội dung JSON phải là một đối tượng.")
    if raw.get("schema_version") == "1.0":
        return _import_single(json_path, media_root)
    if str(raw.get("schema_version")) not in {"2.0", "2.1", "2.2"}:
        raise ProjectValidationError("Tool chỉ hỗ trợ JSON schema_version 1.0, 2.0, 2.1 hoặc 2.2.")
    return _import_batch(json_path, raw, media_root)


def _import_single(json_path: Path, override: str | Path | None) -> BatchImport:
    project = load_project(json_path)
    default_source = project.default_source_path()
    root = _resolve_root(json_path, override, str(default_source.parent))
    source = _find_source(root, str(project.raw["source_video"]), project.project_id)
    if default_source.is_file():
        source = default_source
        root = default_source.parent
    output_root = root / "recaps_da_render"
    output_type = str(project.raw["outputs"][0].get("type") or "")
    recap_mode = "FULL_EPISODE" if len(project.raw["outputs"]) == 1 and output_type == "FULL_RECAP" else "MAIN_STORIES"
    output_counts = {recap_mode: project.output_count}
    return BatchImport(
        json_path=json_path,
        project_name=project.project_id,
        media_root=root,
        output_root=output_root,
        episodes=(
            ImportedEpisode(
                episode_id=project.project_id,
                title=project.project_id,
                source_file=Path(str(project.raw["source_video"])).name,
                source_path=source,
                manifest_path=json_path,
                recap_mode=recap_mode,
                recap_language=project.recap_language,
                output_count=project.output_count,
                available_recap_modes=(recap_mode,),
                output_counts_by_mode=output_counts,
            ),
        ),
    )


def _import_batch(json_path: Path, raw: dict[str, Any], override: str | Path | None) -> BatchImport:
    episodes_raw = raw.get("episodes")
    if not isinstance(episodes_raw, list) or not episodes_raw:
        raise ProjectValidationError("JSON cả mùa phải có danh sách episodes.")
    root = _resolve_root(json_path, override, str(raw.get("media_root_hint") or ""))
    output_name = _safe_folder_name(str(raw.get("output_subdirectory") or "recaps_da_render"))
    output_root = root / output_name
    project_name = str(raw.get("project_name") or json_path.stem).strip()
    batch_key = hashlib.sha256(str(json_path).casefold().encode("utf-8")).hexdigest()[:16]
    manifest_root = default_data_directory() / "manifests" / batch_key
    manifest_root.mkdir(parents=True, exist_ok=True)

    imported: list[ImportedEpisode] = []
    seen: set[str] = set()
    for index, episode in enumerate(episodes_raw, start=1):
        if not isinstance(episode, dict):
            raise ProjectValidationError(f"Tập thứ {index} không hợp lệ.")
        episode_id = _safe_id(str(episode.get("episode_id") or f"episode-{index:03d}"))
        if episode_id in seen:
            raise ProjectValidationError(f"episode_id bị trùng: {episode_id}")
        seen.add(episode_id)
        source_file = str(episode.get("source_file") or episode.get("source_video") or "").strip()
        if not source_file:
            raise ProjectValidationError(f"{episode_id} thiếu source_file.")
        language = str(
            episode.get("recap_language")
            or (episode.get("classification") or {}).get("recap_language")
            or raw.get("recap_language")
            or ""
        )
        if language not in SUPPORTED_LANGUAGES:
            raise ProjectValidationError(
                f"{episode_id} dùng ngôn ngữ không còn được hỗ trợ: {language}. "
                "Bản tối ưu chỉ hỗ trợ tiếng Anh bằng VoiceStudio (en-US hoặc en-GB)."
            )
        outputs = episode.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            raise ProjectValidationError(f"{episode_id} chưa có outputs.")
        output_counts = _output_counts_by_mode(outputs)
        available_modes = tuple(mode for mode in ("FULL_EPISODE", "MAIN_STORIES") if output_counts[mode] > 0)
        declared_modes = episode.get("available_recap_modes")
        if declared_modes is not None:
            if not isinstance(declared_modes, list) or not declared_modes:
                raise ProjectValidationError(f"{episode_id} có available_recap_modes không hợp lệ.")
            normalized_declared = tuple(dict.fromkeys(str(item).upper() for item in declared_modes))
            if any(item not in {"FULL_EPISODE", "MAIN_STORIES"} for item in normalized_declared):
                raise ProjectValidationError(f"{episode_id} khai báo chế độ recap chưa được hỗ trợ.")
            if set(normalized_declared) != set(available_modes):
                raise ProjectValidationError(
                    f"{episode_id} khai báo available_recap_modes không khớp với các loại output thực tế."
                )
            available_modes = normalized_declared
        if output_counts["FULL_EPISODE"] > 1:
            raise ProjectValidationError(f"{episode_id} chỉ được có đúng một output FULL_RECAP.")
        recap_mode = str(episode.get("recap_mode") or raw.get("recap_mode") or "").upper()
        if recap_mode not in {"FULL_EPISODE", "MAIN_STORIES"}:
            recap_mode = "MAIN_STORIES" if "MAIN_STORIES" in available_modes else "FULL_EPISODE"
        if recap_mode not in available_modes:
            raise ProjectValidationError(f"{episode_id} chọn {recap_mode} nhưng JSON không có output tương ứng.")
        source = _find_source(root, source_file, episode_id)
        manifest = _episode_manifest(raw, episode, episode_id, source or Path(source_file), language, recap_mode)
        manifest_path = manifest_root / f"{episode_id}.json"
        temporary = manifest_path.with_suffix(".partial")
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, manifest_path)
        validated = load_project(manifest_path)
        imported.append(
            ImportedEpisode(
                episode_id=episode_id,
                title=str(episode.get("title") or episode_id),
                source_file=Path(source_file).name,
                source_path=source,
                manifest_path=manifest_path,
                recap_mode=recap_mode,
                recap_language=language,
                output_count=output_counts[recap_mode],
                available_recap_modes=available_modes,
                output_counts_by_mode=output_counts,
            )
        )
    return BatchImport(json_path, project_name, root, output_root, tuple(imported))


def _episode_manifest(
    batch: dict[str, Any],
    episode: dict[str, Any],
    episode_id: str,
    source: Path,
    language: str,
    recap_mode: str,
) -> dict[str, Any]:
    classification = dict(episode.get("classification") or {})
    content_type = str(classification.get("content_type") or episode.get("content_type") or batch.get("content_type") or "OTHER").upper()
    aliases = {
        "TV_SHOW": "US_TV_SHOW",
        "GERMAN_SOAP": "DE_GERMAN_SOAP",
        "MOVIE": "FEATURE_FILM",
        "FILM": "FEATURE_FILM",
        "BODY_CAM": "BODYCAM",
        "POLICE_BODYCAM": "BODYCAM",
    }
    classification["content_type"] = aliases.get(
        content_type,
        content_type if content_type in {"US_TV_SHOW", "DE_GERMAN_SOAP", "FEATURE_FILM", "BODYCAM", "OTHER"} else "OTHER",
    )
    classification.setdefault("source_language", str(episode.get("source_language") or language))
    classification["recap_language"] = language
    classification.setdefault("confidence", 1.0)
    classification.setdefault("reason", "Dữ liệu do ChatGPT phân tích và cung cấp.")
    outputs = json.loads(json.dumps(episode["outputs"], ensure_ascii=False))
    for output_index, output in enumerate(outputs, start=1):
        output.setdefault("render_id", f"noi-dung-{output_index:02d}")
        output.setdefault("type", "FULL_RECAP" if recap_mode == "FULL_EPISODE" else "MAIN_STORY")
        output.setdefault("title", output["render_id"])
        title = " ".join(str(output["title"]).split()).strip(". ")
        output["title"] = title
        output["file_name"] = f"{title}.mp4"
        output["narration_subtitle_file"] = f"{title}.narration.srt"
        output["original_subtitle_file"] = f"{title}.original.srt"
        for segment_index, segment in enumerate(output.get("segments") or [], start=1):
            segment.setdefault("segment_id", f"{episode_id}-segment-{output_index:02d}-{segment_index:03d}")
            segment.setdefault("order", segment_index)
            segment.setdefault("segment_type", "narration")
            segment.setdefault("purpose", "PLOT_DESCRIPTION")
            segment.setdefault("estimated_voice_duration_ms", 0)
            segment.setdefault("subtitle", True)
            if segment["segment_type"] == "narration":
                segment["subtitle_source"] = "narration"
                segment["original_audio"] = "mute"
                segment["audio_policy"] = "mute"
                segment["preserve_original_audio"] = False
            else:
                segment["subtitle_source"] = "original_dialogue"
                segment.setdefault("original_dialogue_text", "")
                segment["narration_text"] = ""
                segment["original_audio"] = "preserve"
                segment["audio_policy"] = "preserve"
                segment["preserve_original_audio"] = True
            for clip_index, clip in enumerate(segment.get("source_clips") or [], start=1):
                clip.setdefault("clip_id", f"{episode_id}-clip-{output_index:02d}-{segment_index:03d}-{clip_index:02d}")
                clip.setdefault("order", clip_index)
                clip["source_video"] = str(source)
            normalize_segment_timing(segment, language)
    policy = dict(episode.get("render_policy") or batch.get("render_policy") or {})
    policy.update(
        {
            "aspect_ratio_policy": "preserve_source",
            "voice_speed": 1.0,
            "allow_frame_freeze": True,
            "allow_clip_repeat": False,
            "allow_unlisted_clips": False,
        }
    )
    policy.setdefault("video_speed_min", 0.9)
    policy.setdefault("video_speed_max", 1.1)
    policy.setdefault("video_speed_absolute_min", 0.75)
    policy.setdefault("video_speed_absolute_max", 1.15)
    manifest = {
        "schema_version": "1.0",
        "project_id": episode_id,
        "source_video": str(source),
        "recap_mode": recap_mode,
        "classification": classification,
        "source_rights": dict(episode.get("source_rights") or {"status": "UNVERIFIED", "notes": "User confirmation required before publishing."}),
        "render_policy": policy,
        "voice_profile": {"language": language, "voice_id": "selected-in-tool", "style": "narration"},
        "outputs": outputs,
    }
    try:
        validate_smooth_edit_plan(manifest, content_type=classification["content_type"])
    except SmoothEditPlanError as exc:
        raise ProjectValidationError(str(exc)) from exc
    return manifest


def _estimate_voice_duration_ms(text: str, language: str) -> int:
    words = re.findall(r"\b[\wÀ-ỹÄÖÜäöüß]+(?:[-'][\wÀ-ỹÄÖÜäöüß]+)*\b", text, flags=re.UNICODE)
    if not words:
        return 0
    words_per_minute = _PLANNING_WORDS_PER_MINUTE.get(language, 140)
    duration_ms = len(words) * 60_000 / words_per_minute
    # Làm tròn lên 100 ms để không tạo độ chính xác giả.
    return int(((duration_ms + 99) // 100) * 100)


def normalize_segment_timing(segment: dict[str, Any], language: str) -> None:
    visual_duration_ms = round(
        sum(
            max(0.0, float(clip.get("end_ms") or 0) - float(clip.get("start_ms") or 0))
            for clip in segment.get("source_clips") or []
        )
    )
    segment["source_visual_duration_ms"] = visual_duration_ms
    if segment.get("segment_type") == "narration":
        fallback_duration_ms = _estimate_voice_duration_ms(
            str(segment.get("narration_text") or ""),
            language,
        )
        try:
            supplied_duration_ms = max(0, round(float(segment.get("estimated_voice_duration_ms") or 0)))
        except (TypeError, ValueError):
            supplied_duration_ms = 0
        # Không dùng estimate quá lạc quan do model tạo. Estimate bảo thủ giúp
        # prompt, JSON xuất ra và renderer cùng lập kế hoạch theo một chuẩn.
        estimated_duration_ms = max(supplied_duration_ms, fallback_duration_ms)
        segment["estimated_voice_duration_ms"] = estimated_duration_ms
        segment["recommended_visual_speed"] = round(
            visual_duration_ms / estimated_duration_ms,
            4,
        ) if estimated_duration_ms else 1.0
    else:
        segment["estimated_voice_duration_ms"] = 0
        segment["recommended_visual_speed"] = 1.0


def _resolve_root(json_path: Path, override: str | Path | None, hint: str) -> Path:
    if override:
        candidate = Path(override).expanduser().resolve()
        if candidate.is_dir():
            return candidate
    if hint:
        candidate = Path(hint).expanduser()
        if not candidate.is_absolute():
            candidate = json_path.parent / candidate
        candidate = candidate.resolve()
        if candidate.is_dir():
            return candidate
    return json_path.parent


def _find_source(root: Path, source_file: str, episode_id: str) -> Path | None:
    requested = Path(source_file)
    direct = requested if requested.is_absolute() else root / requested
    if direct.is_file():
        return direct.resolve()
    target_name = requested.name.casefold()
    videos = [item for item in root.rglob("*") if item.is_file() and item.suffix.casefold() in VIDEO_EXTENSIONS]
    exact = [item for item in videos if item.name.casefold() == target_name]
    if len(exact) == 1:
        return exact[0].resolve()
    episode_matches = [item for item in videos if episode_id.casefold() in item.stem.casefold()]
    if len(episode_matches) == 1:
        return episode_matches[0].resolve()
    return None


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return cleaned or "episode"


def _safe_folder_name(value: str) -> str:
    name = Path(value).name.strip().strip(". ")
    return name or "recaps_da_render"


def _output_counts_by_mode(outputs: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "FULL_EPISODE": sum(str(item.get("type") or "") == "FULL_RECAP" for item in outputs),
        "MAIN_STORIES": sum(str(item.get("type") or "") == "MAIN_STORY" for item in outputs),
    }
