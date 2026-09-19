from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ProjectValidationError(ValueError):
    """Lỗi dữ liệu dự án có thể hiển thị trực tiếp bằng tiếng Việt."""


@dataclass(frozen=True)
class ProjectData:
    path: Path
    raw: dict[str, Any]

    @property
    def project_id(self) -> str:
        return str(self.raw["project_id"])

    @property
    def recap_language(self) -> str:
        return str(self.raw["classification"]["recap_language"])

    @property
    def content_type(self) -> str:
        return str(self.raw["classification"]["content_type"])

    @property
    def output_count(self) -> int:
        return len(self.raw["outputs"])

    @property
    def segment_count(self) -> int:
        return sum(len(item["segments"]) for item in self.raw["outputs"])

    def default_source_path(self) -> Path:
        source = Path(str(self.raw["source_video"]))
        if source.is_absolute():
            return source
        return (self.path.parent / source).resolve()


_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_CONTENT_TYPES = {"US_TV_SHOW", "DE_GERMAN_SOAP", "FEATURE_FILM", "BODYCAM", "OTHER"}
_LANGUAGES = {"en-US", "en-GB"}
_OUTPUT_TYPES = {"FULL_RECAP", "HIGHLIGHT", "MAIN_STORY"}
_SEGMENT_TYPES = {"narration", "original_dialogue"}
_AUDIO_POLICIES = {"mute", "duck", "preserve"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProjectValidationError(message)


def load_project(path: str | Path) -> ProjectData:
    project_path = Path(path).expanduser().resolve()
    _require(project_path.is_file(), f"Không tìm thấy file chỉ dẫn: {project_path}")

    try:
        raw = json.loads(project_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ProjectValidationError(
            f"File JSON không hợp lệ tại dòng {exc.lineno}, cột {exc.colno}: {exc.msg}"
        ) from exc

    _require(isinstance(raw, dict), "Nội dung JSON phải là một đối tượng.")
    _require(raw.get("schema_version") == "1.0", "Tool chỉ hỗ trợ schema_version 1.0.")

    project_id = raw.get("project_id")
    _require(isinstance(project_id, str) and bool(project_id.strip()), "Thiếu project_id.")
    _require(bool(_SAFE_ID.fullmatch(project_id)), "project_id chỉ được chứa chữ, số, dấu chấm, gạch ngang hoặc gạch dưới.")

    _require(isinstance(raw.get("source_video"), str) and raw["source_video"].strip(), "Thiếu source_video.")

    classification = raw.get("classification")
    _require(isinstance(classification, dict), "Thiếu classification.")
    _require(classification.get("content_type") in _CONTENT_TYPES, "content_type không được hỗ trợ.")
    _require(classification.get("recap_language") in _LANGUAGES, "Ngôn ngữ recap chưa được hỗ trợ.")

    policy = raw.setdefault("render_policy", {})
    _require(isinstance(policy, dict), "render_policy phải là một đối tượng.")
    _require(policy.get("aspect_ratio_policy", "preserve_source") == "preserve_source", "Tool chỉ chấp nhận preserve_source.")
    policy.setdefault("voice_speed", 1.0)
    _require(float(policy["voice_speed"]) == 1.0, "Tốc độ giọng đọc bắt buộc là 1.00x.")
    policy.setdefault("video_speed_min", 0.9)
    policy.setdefault("video_speed_max", 1.1)
    policy.setdefault("video_speed_absolute_min", 0.75)
    policy.setdefault("video_speed_absolute_max", 1.15)
    minimum = float(policy["video_speed_absolute_min"])
    maximum = float(policy["video_speed_absolute_max"])
    _require(0.1 <= minimum <= 1.0 <= maximum <= 4.0, "Giới hạn tốc độ video không hợp lệ.")

    outputs = raw.get("outputs")
    _require(isinstance(outputs, list) and outputs, "Dự án phải có ít nhất một video đầu ra.")

    render_ids: set[str] = set()
    output_titles: set[str] = set()
    global_segment_ids: set[str] = set()
    global_clip_ids: set[str] = set()
    for output_index, output in enumerate(outputs, start=1):
        _require(isinstance(output, dict), f"Video đầu ra thứ {output_index} không hợp lệ.")
        render_id = output.get("render_id")
        _require(isinstance(render_id, str) and _SAFE_ID.fullmatch(render_id) is not None, f"render_id thứ {output_index} không hợp lệ.")
        _require(render_id not in render_ids, f"render_id bị trùng: {render_id}")
        render_ids.add(render_id)
        _require(output.get("type") in _OUTPUT_TYPES, f"Loại output của {render_id} không hợp lệ.")
        title = " ".join(str(output.get("title") or "").split()).strip(". ")
        _require(bool(title), f"{render_id} thiếu title để đặt tên video.")
        _require(re.search(r'[<>:"/\\|?*\x00-\x1f]', title) is None, f"Title của {render_id} chứa ký tự không hợp lệ cho tên file.")
        _require(title.casefold() not in output_titles, f"Title output bị trùng: {title}")
        output_titles.add(title.casefold())
        output["title"] = title
        output["file_name"] = f"{title}.mp4"
        output["narration_subtitle_file"] = f"{title}.narration.srt"
        output["original_subtitle_file"] = f"{title}.original.srt"

        segments = output.get("segments")
        _require(isinstance(segments, list) and segments, f"{render_id} chưa có phân đoạn.")
        orders: set[int] = set()
        for segment_index, segment in enumerate(segments, start=1):
            _require(isinstance(segment, dict), f"Phân đoạn {segment_index} trong {render_id} không hợp lệ.")
            segment_id = segment.get("segment_id")
            _require(isinstance(segment_id, str) and _SAFE_ID.fullmatch(segment_id) is not None, f"segment_id trong {render_id} không hợp lệ.")
            _require(segment_id not in global_segment_ids, f"segment_id bị trùng: {segment_id}")
            global_segment_ids.add(segment_id)
            order = segment.get("order")
            _require(isinstance(order, int) and order > 0, f"order của {segment_id} phải là số nguyên dương.")
            _require(order not in orders, f"Thứ tự phân đoạn bị trùng trong {render_id}: {order}")
            orders.add(order)

            segment_type = segment.get("segment_type")
            _require(segment_type in _SEGMENT_TYPES, f"segment_type của {segment_id} không hợp lệ.")
            narration_text = segment.get("narration_text")
            if segment_type == "narration":
                _require(isinstance(narration_text, str) and narration_text.strip(), f"{segment_id} thiếu lời đọc.")
            else:
                _require(narration_text in (None, ""), f"{segment_id} là hội thoại gốc nên narration_text phải rỗng.")

            audio_policy = segment.get("audio_policy", segment.get("original_audio", "mute" if segment_type == "narration" else "preserve"))
            _require(audio_policy in _AUDIO_POLICIES, f"original_audio của {segment_id} không hợp lệ.")
            if segment_type == "narration":
                _require(audio_policy == "mute", f"{segment_id} có lời recap nên âm thanh phim bắt buộc phải tắt.")
                _require(segment.get("preserve_original_audio", False) is False, f"{segment_id} không được giữ âm thanh phim dưới lời recap.")
            else:
                _require(audio_policy == "preserve", f"{segment_id} là âm thanh gốc nên phải dùng preserve.")
            segment["original_audio"] = audio_policy

            clips = segment.get("source_clips")
            _require(isinstance(clips, list) and clips, f"{segment_id} chưa có clip nguồn.")
            clip_orders: set[int] = set()
            for clip_index, clip in enumerate(clips, start=1):
                _require(isinstance(clip, dict), f"Clip {clip_index} trong {segment_id} không hợp lệ.")
                clip_id = clip.get("clip_id")
                _require(isinstance(clip_id, str) and _SAFE_ID.fullmatch(clip_id) is not None, f"clip_id trong {segment_id} không hợp lệ.")
                _require(clip_id not in global_clip_ids, f"clip_id bị trùng: {clip_id}")
                global_clip_ids.add(clip_id)
                clip_order = clip.get("order")
                _require(isinstance(clip_order, int) and clip_order > 0, f"order của {clip_id} phải là số nguyên dương.")
                _require(clip_order not in clip_orders, f"Thứ tự clip bị trùng trong {segment_id}: {clip_order}")
                clip_orders.add(clip_order)
                start_ms = clip.get("start_ms")
                end_ms = clip.get("end_ms")
                _require(isinstance(start_ms, (int, float)) and start_ms >= 0, f"start_ms của {clip_id} không hợp lệ.")
                _require(isinstance(end_ms, (int, float)) and end_ms > start_ms, f"end_ms của {clip_id} phải lớn hơn start_ms.")

    return ProjectData(path=project_path, raw=raw)


def ordered_outputs(project: ProjectData) -> list[dict[str, Any]]:
    return list(project.raw["outputs"])


def ordered_segments(output: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(output["segments"], key=lambda item: item["order"])


def ordered_clips(segment: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(segment["source_clips"], key=lambda item: item["order"])
