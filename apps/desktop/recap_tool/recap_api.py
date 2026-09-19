from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont

from .api_client import OpenAICompatibleClient
from .batch import VIDEO_EXTENSIONS, import_recap_json, normalize_segment_timing
from .credentials import default_data_directory
from .gpu import application_root
from .media import MediaError, extract_frame, find_binary, probe_media, run_command
from .originality import audit_episode
from .settings import AppSettings
from .edit_plan import validate_smooth_edit_plan
from .speech_to_text import transcribe_video_audio


LogFn = Callable[[str], None]
ProgressFn = Callable[[int, int, str], None]


DEFAULT_RECAP_PROMPT = """
Bạn là biên kịch kiêm dựng phim cho Recap Studio. Phân tích toàn bộ evidence của
tập phim và tạo edit decision plan trung thực, có mạch nhân quả rõ ràng. Narration
phải tự nhiên ở đúng ngôn ngữ yêu cầu, không viết kiểu báo cáo "trong cảnh này".
Không bịa nhân vật, lời thoại, sự kiện hoặc timecode. Chọn footage trực tiếp minh
họa câu đang kể; loại opening, credit, recap đầu tập, preview và cảnh lặp. Chỉ trả
về JSON đúng schema Recap Studio được cung cấp.
""".strip()


SCANNER_SYSTEM = """
You are the evidence scanner for a movie/TV recap pipeline. Review the exact
timestamped contact sheet and transcript excerpt. Identify story events, causal
links, character actions, reveals, emotional changes, comedy/action beats and
likely exclusions. Do not invent dialogue or identities. Return JSON only:
{
  "range_start_ms": 0,
  "range_end_ms": 0,
  "events": [
    {
      "start_ms": 0,
      "end_ms": 0,
      "summary": "source-grounded event",
      "characters": ["name or neutral appearance label"],
      "importance": 0.0,
      "causes": ["earlier event if supported"],
      "dialogue_evidence": ["short paraphrase"],
      "visual_evidence": ["visible action/location"],
      "exclude": false
    }
  ]
}
All timestamps must stay inside the supplied absolute range.
""".strip()


BODYCAM_SCANNER_SYSTEM = """
You are the evidence scanner for a bodycam/dashcam incident editing pipeline.
Review only the supplied timestamped contact sheet and transcript excerpt. Treat
the camera as a limited viewpoint. Do not infer events before recording, actions
outside frame, identity, motive, intoxication, mental state, guilt, legality, or
criminal history. Distinguish direct observation from a participant's statement.
Use neutral role labels unless a supplied authoritative context explicitly
verifies a public identity. Preserve the context around questions, commands,
responses, escalation, de-escalation, restraint, medical response and outcome.
Flag obscured video, unclear speakers and edit-sensitive dialogue. Return JSON only:
{
  "range_start_ms": 0,
  "range_end_ms": 0,
  "events": [
    {
      "start_ms": 0,
      "end_ms": 0,
      "summary": "source-grounded neutral event",
      "participants": ["Officer 1", "Driver", "Unknown speaker"],
      "claim_type": "OBSERVED | STATED | VERIFIED_OUTCOME",
      "speaker": "role label or empty",
      "importance": 0.0,
      "dialogue_evidence": ["short paraphrase, not invented quotation"],
      "visual_evidence": ["visible action or camera limitation"],
      "context_required_before": true,
      "camera_obscured": false,
      "confidence": 0.0,
      "exclude": false
    }
  ]
}
All timestamps must stay inside the supplied absolute range.
""".strip()


FINAL_SUFFIX = """

Return one complete episode object only, with episode_id, title, source_file,
recap_mode, recap_language and outputs. Follow the supplied JSON Schema definitions
for episode/output/segment/clip. Every clip uses integer milliseconds on the original
source timeline. narration segments use audio_policy=mute and
preserve_original_audio=false. original_dialogue segments contain empty narration,
audio_policy=preserve, preserve_original_audio=true, and original_dialogue_text
transcribed exactly from the supplied source evidence. Each output title is also
the exact publication filename base and must define file_name=<title>.mp4,
narration_subtitle_file=<title>.narration.srt, and
original_subtitle_file=<title>.original.srt. Return JSON only, without
Markdown or commentary.
""".rstrip()


def default_recap_prompt() -> str:
    candidates = [application_root() / "PROMPT_RECAP_TVSHOW_ORIGINAL_FACEBOOK.md"]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.insert(0, Path(sys._MEIPASS) / "PROMPT_RECAP_TVSHOW_ORIGINAL_FACEBOOK.md")
    for prompt_file in candidates:
        if not prompt_file.is_file():
            continue
        try:
            return prompt_file.read_text(encoding="utf-8-sig")
        except OSError:
            continue
    return DEFAULT_RECAP_PROMPT


def recap_prompt_for_content(content_type: str) -> str:
    normalized = str(content_type).upper()
    prompt_names = {
        "DE_GERMAN_SOAP": "PROMPT_RECAP_GERMAN_SOAP_HOAN_CHINH.md",
        "BODYCAM": "PROMPT_BODYCAM_EVIDENCE_COMMENTARY_HOAN_CHINH.md",
    }
    prompt_name = prompt_names.get(normalized)
    if prompt_name:
        candidates = [application_root() / prompt_name]
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            candidates.insert(0, Path(sys._MEIPASS) / prompt_name)
        for content_prompt in candidates:
            if not content_prompt.is_file():
                continue
            try:
                return content_prompt.read_text(encoding="utf-8-sig")
            except OSError:
                continue
    return default_recap_prompt()


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return cleaned or "episode"


def _episode_id(video: Path, index: int) -> str:
    match = re.search(r"(?i)(S\d{1,2}[ ._-]*E\d{1,3})", video.stem)
    if match:
        return re.sub(r"[ ._-]+", "", match.group(1)).upper()
    return _safe_id(video.stem) if index == 1 else f"EP{index:03d}"


def enumerate_videos(source: Path) -> list[Path]:
    if source.is_file() and source.suffix.casefold() in VIDEO_EXTENSIONS:
        return [source.resolve()]
    if not source.is_dir():
        return []
    return sorted(
        (item.resolve() for item in source.rglob("*") if item.is_file() and item.suffix.casefold() in VIDEO_EXTENSIONS),
        key=lambda item: item.name.casefold(),
    )


def _fingerprint(video: Path) -> str:
    stat = video.stat()
    value = f"{video.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:20]


def _format_time(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def _chunk_ranges(duration: float, chunk_seconds: int) -> list[tuple[float, float]]:
    ranges = []
    start = 0.0
    while start < duration:
        end = min(duration, start + max(60, chunk_seconds))
        ranges.append((start, end))
        start = end
    return ranges


def _frame_times(start: float, end: float, count: int = 8) -> list[float]:
    if end <= start:
        return [start]
    duration = end - start
    leading_margin = min(1.0, max(0.05, duration * 0.03))
    trailing_margin = min(1.0, max(0.25, duration * 0.03))
    lo = start + min(leading_margin, duration / 3)
    hi = max(lo, end - min(trailing_margin, duration / 3))
    if count <= 1:
        return [(lo + hi) / 2]
    return [lo + (hi - lo) * index / (count - 1) for index in range(count)]


def _make_contact_sheet(
    video: Path,
    start: float,
    end: float,
    destination: Path,
    cancel_event: threading.Event,
) -> Path:
    if destination.is_file():
        return destination
    frame_dir = destination.parent / (destination.stem + "_frames")
    frame_dir.mkdir(parents=True, exist_ok=True)
    frames: list[tuple[Path, float]] = []
    for index, timestamp in enumerate(_frame_times(start, end), start=1):
        if cancel_event.is_set():
            raise RuntimeError("Đã dừng phân tích.")
        frame = frame_dir / f"{index:02d}.png"
        if not frame.is_file():
            extract_frame(video, timestamp, frame, width=760, cancel_event=cancel_event)
        frames.append((frame, timestamp))
    columns, thumb_width, thumb_height, label_height = 4, 400, 225, 34
    rows = (len(frames) + columns - 1) // columns
    canvas = Image.new("RGB", (columns * thumb_width, rows * (thumb_height + label_height)), "#111111")
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.load_default(size=18)
    except TypeError:
        font = ImageFont.load_default()
    for index, (frame, timestamp) in enumerate(frames):
        with Image.open(frame) as image:
            image.thumbnail((thumb_width, thumb_height), Image.Resampling.LANCZOS)
            x = (index % columns) * thumb_width
            y = (index // columns) * (thumb_height + label_height)
            canvas.paste(image.convert("RGB"), (x + (thumb_width - image.width) // 2, y))
            draw.text((x + 8, y + thumb_height + 7), _format_time(timestamp), fill="white", font=font)
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, quality=90)
    return destination


def _subtitle_sidecar(video: Path) -> Path | None:
    exact = [video.with_suffix(ext) for ext in (".srt", ".vtt", ".ass", ".ssa")]
    for candidate in exact:
        if candidate.is_file():
            return candidate
    matches = sorted(
        item for item in video.parent.iterdir()
        if item.is_file() and item.suffix.casefold() in {".srt", ".vtt", ".ass", ".ssa"}
        and item.stem.casefold().startswith(video.stem.casefold())
    )
    return matches[0] if matches else None


def _subtitle_as_srt(video: Path, cache_dir: Path, cancel_event: threading.Event, log: LogFn) -> Path | None:
    source = _subtitle_sidecar(video)
    destination = cache_dir / "subtitles.srt"
    if destination.is_file() and destination.stat().st_size:
        return destination
    if source and source.suffix.casefold() == ".srt":
        return source
    try:
        if source:
            run_command([find_binary("ffmpeg"), "-y", "-i", str(source), str(destination)], cancel_event=cancel_event)
        else:
            run_command(
                [find_binary("ffmpeg"), "-y", "-i", str(video), "-map", "0:s:0", "-c:s", "srt", str(destination)],
                cancel_event=cancel_event,
            )
        return destination if destination.is_file() and destination.stat().st_size else None
    except MediaError:
        log(f"{video.name}: không có phụ đề dùng được.")
        return None


def _timestamp_seconds(value: str) -> float:
    normalized = value.strip().replace(",", ".")
    parts = normalized.split(":")
    if len(parts) == 2:
        parts.insert(0, "0")
    hours, minutes, seconds = int(parts[0]), int(parts[1]), float(parts[2])
    return hours * 3600 + minutes * 60 + seconds


def _parse_subtitles(path: Path | None) -> list[dict[str, Any]]:
    if not path:
        return []
    text = path.read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n")
    blocks = re.split(r"\n\s*\n", text)
    entries = []
    timing = re.compile(r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?[,.]\d{3})\s*-->\s*(?P<end>\d{1,2}:\d{2}(?::\d{2})?[,.]\d{3})")
    for block in blocks:
        match = timing.search(block)
        if not match:
            continue
        lines = block[match.end() :].strip().splitlines()
        content = " ".join(line.strip() for line in lines if line.strip())
        content = html.unescape(re.sub(r"<[^>]+>|\{\\[^}]+\}", "", content)).strip()
        if content:
            entries.append({"start": _timestamp_seconds(match.group("start")), "end": _timestamp_seconds(match.group("end")), "text": content})
    return entries


def _transcript_slice(entries: list[dict[str, Any]], start: float, end: float, limit: int = 16_000) -> str:
    lines = [
        f'[{_format_time(item["start"])} - {_format_time(item["end"])}] {item["text"]}'
        for item in entries
        if item["end"] >= start and item["start"] <= end
    ]
    return "\n".join(lines)[:limit]


def _scan_chunk(
    *,
    video: Path,
    episode_id: str,
    index: int,
    start: float,
    end: float,
    transcript: list[dict[str, Any]],
    transcript_source: str,
    cache_dir: Path,
    client: OpenAICompatibleClient,
    settings: AppSettings,
    prompt: str,
    content_type: str,
    cancel_event: threading.Event,
) -> dict[str, Any]:
    chunk_transcript = _transcript_slice(transcript, start, end)
    transcript_tag = hashlib.sha256(chunk_transcript.encode("utf-8")).hexdigest()
    prompt_tag = hashlib.sha256(
        (
            f"{settings.api_endpoint}|{settings.scanner_model}|{settings.scanner_thinking}|"
            f"{settings.api_chunk_seconds}|{start:.3f}|{end:.3f}|{prompt}|"
            f"{transcript_source}|{transcript_tag}|{content_type}"
        ).encode("utf-8")
    ).hexdigest()[:12]
    result_path = cache_dir / "scan" / prompt_tag / f"{index:04d}.json"
    if result_path.is_file():
        return json.loads(result_path.read_text(encoding="utf-8"))
    sheet = cache_dir / "sheets" / f"{index:04d}_{round(start * 1000)}_{round(end * 1000)}.jpg"
    _make_contact_sheet(video, start, end, sheet, cancel_event)
    user_text = (
        f"Episode: {episode_id}\nSource file: {video.name}\n"
        f"Absolute range: {start * 1000:.0f} to {end * 1000:.0f} ms "
        f"({_format_time(start)} - {_format_time(end)})\n\n"
        f"Recap-specific instructions (scanner excerpt):\n{prompt[:6000]}\n\n"
        f"Timestamped transcript source: {transcript_source}\n"
        f"Transcript/subtitles:\n{chunk_transcript or '[No speech or subtitle text available]'}\n\n"
        "The attached contact sheet contains timestamp labels from this exact range."
    )
    result = client.chat_json(
        model=settings.scanner_model,
        thinking=settings.scanner_thinking,
        system=BODYCAM_SCANNER_SYSTEM if content_type == "BODYCAM" else SCANNER_SYSTEM,
        user_text=user_text,
        images=[sheet],
        max_tokens=10_000,
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = result_path.with_suffix(".partial")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, result_path)
    return result


def _schema() -> dict[str, Any]:
    candidates = [application_root() / "schemas" / "recap-project-batch-v2.json"]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.insert(0, Path(sys._MEIPASS) / "schemas" / "recap-project-batch-v2.json")
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        raise RuntimeError(f"Thiếu JSON Schema Recap Studio: {candidates[0]}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _normalize_episode(raw: dict[str, Any], episode_id: str, video: Path, mode: str, language: str) -> dict[str, Any]:
    if isinstance(raw.get("episode"), dict):
        episode = dict(raw["episode"])
    elif isinstance(raw.get("episodes"), list) and raw["episodes"] and isinstance(raw["episodes"][0], dict):
        episode = dict(raw["episodes"][0])
    else:
        episode = dict(raw)
    episode["episode_id"] = episode_id
    episode["source_file"] = video.name
    episode["recap_mode"] = mode
    episode["recap_language"] = language
    episode.setdefault(
        "source_rights",
        {"status": "UNVERIFIED", "notes": "User confirmation required before publishing."},
    )
    desired = "FULL_RECAP" if mode == "FULL_EPISODE" else "MAIN_STORY"
    outputs = [item for item in episode.get("outputs", []) if isinstance(item, dict) and item.get("type") == desired]
    if not outputs:
        raise ValueError(f"API không trả về output loại {desired}.")
    for output in outputs:
        title = " ".join(str(output.get("title") or "").split()).strip(". ")
        if not title:
            raise ValueError("Mỗi output phải có title để đặt tên video.")
        output["title"] = title
        output["file_name"] = f"{title}.mp4"
        output["narration_subtitle_file"] = f"{title}.narration.srt"
        output["original_subtitle_file"] = f"{title}.original.srt"
        for segment in output.get("segments") or []:
            if isinstance(segment, dict):
                if segment.get("segment_type") == "original_dialogue":
                    segment["subtitle_source"] = "original_dialogue"
                    segment.setdefault("original_dialogue_text", "")
                else:
                    segment["subtitle_source"] = "narration"
                normalize_segment_timing(segment, language)
    episode["outputs"] = outputs
    episode["available_recap_modes"] = [mode]
    return episode


def _finalize_episode(
    *,
    video: Path,
    episode_id: str,
    mode: str,
    language: str,
    content_type: str,
    transcript: list[dict[str, Any]],
    transcript_source: str,
    scans: list[dict[str, Any]],
    client: OpenAICompatibleClient,
    settings: AppSettings,
    prompt: str,
    source_rights_status: str,
    log: LogFn,
) -> dict[str, Any]:
    context = {
        "episode_id": episode_id,
        "source_file": video.name,
        "duration_ms": round(probe_media(video)["duration"] * 1000),
        "recap_mode": mode,
        "recap_language": language,
        "content_type": content_type,
        "source_rights": {
            "status": source_rights_status,
            "notes": "Declared by the user in Recap Studio before analysis.",
        },
        "chunk_evidence": scans,
        "transcript_source": transcript_source,
        "timestamped_transcript": transcript,
        "schema": _schema(),
    }
    previous: dict[str, Any] | None = None
    failure = ""
    for attempt in range(3):
        correction = ""
        if previous is not None:
            correction = f"\n\nPrevious JSON failed: {failure}\nPrevious JSON:\n{json.dumps(previous, ensure_ascii=False)}"
        log(
            f"{episode_id}: Finalizer {settings.finalizer_model} đang biên soạn JSON, "
            f"lần {attempt + 1}/3"
        )
        previous = client.chat_json(
            model=settings.finalizer_model,
            thinking=settings.finalizer_thinking,
            system=prompt + FINAL_SUFFIX,
            user_text="Create the episode recap object from this evidence:\n" + json.dumps(context, ensure_ascii=False, separators=(",", ":")) + correction,
            max_tokens=48_000,
        )
        try:
            episode = _normalize_episode(previous, episode_id, video, mode, language)
            validate_smooth_edit_plan(episode, content_type=content_type)
            missing_dialogue_text = [
                str(segment.get("segment_id") or "unknown-segment")
                for output in episode.get("outputs") or []
                for segment in output.get("segments") or []
                if segment.get("segment_type") == "original_dialogue"
                and not str(segment.get("original_dialogue_text") or "").strip()
            ]
            if missing_dialogue_text:
                raise ValueError(
                    "Các original_dialogue segment thiếu original_dialogue_text: "
                    + ", ".join(missing_dialogue_text)
                )
            episode["source_rights"] = {
                "status": source_rights_status,
                "notes": "Declared by the user in Recap Studio before analysis.",
            }
            originality = audit_episode(episode, content_type=content_type, recap_mode=mode)
            if not originality.passes:
                details = "; ".join(issue.message for issue in originality.blocking_issues[:8])
                raise ValueError("Originality gate chưa đạt: " + details)
            validation_document = {
                "schema_version": "2.2",
                "project_type": "SINGLE_EPISODE",
                "project_name": f"validation-{episode_id}",
                "media_root_hint": str(video.parent),
                "output_subdirectory": "recaps_da_render",
                "content_type": content_type,
                "recap_language": language,
                "episodes": [episode],
            }
            validation_path = default_data_directory() / "api-analysis" / "validation" / f"{_fingerprint(video)}.json"
            validation_path.parent.mkdir(parents=True, exist_ok=True)
            validation_path.write_text(json.dumps(validation_document, ensure_ascii=False, indent=2), encoding="utf-8")
            import_recap_json(validation_path, video.parent)
            return episode
        except Exception as exc:
            failure = str(exc)
    raise RuntimeError(f"{episode_id}: API không tạo được episode JSON hợp lệ: {failure}")


def analyze_input(
    source: Path,
    *,
    project_name: str,
    language: str,
    mode: str,
    content_type: str,
    settings: AppSettings,
    prompt: str,
    log: LogFn,
    progress: ProgressFn,
    cancel_event: threading.Event,
    source_rights_status: str = "UNVERIFIED",
) -> Path:
    source = source.expanduser().resolve()
    source_rights_status = str(source_rights_status).upper()
    if source_rights_status not in {"UNVERIFIED", "OWNED", "LICENSED", "FIRST_PUBLICATION_RIGHTS"}:
        raise ValueError("Trạng thái quyền footage không hợp lệ.")
    videos = enumerate_videos(source)
    if not videos:
        raise RuntimeError("Không tìm thấy video được hỗ trợ.")
    client = OpenAICompatibleClient(settings.api_endpoint, settings.api_key)
    episodes = []
    total = len(videos)
    for video_index, video in enumerate(videos, start=1):
        if cancel_event.is_set():
            raise RuntimeError("Đã dừng phân tích.")
        episode_id = _episode_id(video, video_index)
        info = probe_media(video)
        cache_dir = default_data_directory() / "api-analysis" / _fingerprint(video)
        cache_dir.mkdir(parents=True, exist_ok=True)
        subtitle_path = _subtitle_as_srt(video, cache_dir, cancel_event, log)
        transcript = _parse_subtitles(subtitle_path)
        transcript_source = "subtitles"
        if transcript:
            log(f"{episode_id}: {len(transcript)} dòng phụ đề, thời lượng {_format_time(info['duration'])}.")
        elif info["has_audio"]:
            progress(video_index - 1, total, f"{episode_id}: đang nhận dạng lời thoại từ âm thanh")
            transcript = transcribe_video_audio(video, cache_dir, cancel_event, log)
            transcript_source = "local_faster_whisper"
            if transcript:
                log(
                    f"{episode_id}: dùng {len(transcript)} đoạn lời thoại có timecode cùng visual evidence."
                )
            else:
                transcript_source = "visual_only_no_speech_detected"
                log(f"{episode_id}: âm thanh không có lời nói nhận dạng được; dùng visual evidence.")
        else:
            transcript_source = "visual_only_no_audio"
            log(f"{episode_id}: video không có phụ đề hoặc âm thanh; dùng visual evidence.")
        ranges = _chunk_ranges(info["duration"], settings.api_chunk_seconds)
        scans: list[dict[str, Any] | None] = [None] * len(ranges)
        log(
            f"{episode_id}: Scanner {settings.scanner_model} phân tích {len(ranges)} đoạn "
            f"(song song {max(1, min(4, settings.scanner_parallelism))})."
        )
        with ThreadPoolExecutor(max_workers=max(1, min(4, settings.scanner_parallelism))) as executor:
            futures = {
                executor.submit(
                    _scan_chunk,
                    video=video,
                    episode_id=episode_id,
                    index=index,
                    start=start,
                    end=end,
                    transcript=transcript,
                    transcript_source=transcript_source,
                    cache_dir=cache_dir,
                    client=client,
                    settings=settings,
                    prompt=prompt,
                    content_type=content_type,
                    cancel_event=cancel_event,
                ): index
                for index, (start, end) in enumerate(ranges)
            }
            completed = 0
            for future in as_completed(futures):
                index = futures[future]
                scans[index] = future.result()
                completed += 1
                progress(
                    video_index - 1,
                    total,
                    f"{episode_id}: Scanner {settings.scanner_model} — {completed}/{len(ranges)} đoạn",
                )
        progress(video_index - 1, total, f"{episode_id}: Finalizer {settings.finalizer_model} đang tổng hợp JSON")
        episode = _finalize_episode(
            video=video,
            episode_id=episode_id,
            mode=mode,
            language=language,
            content_type=content_type,
            transcript=transcript,
            transcript_source=transcript_source,
            scans=[item for item in scans if item is not None],
            client=client,
            settings=settings,
            prompt=prompt,
            source_rights_status=source_rights_status,
            log=log,
        )
        episodes.append(episode)
        progress(video_index, total, f"Hoàn tất {episode_id}")

    media_root = source if source.is_dir() else source.parent
    name = project_name.strip() or media_root.name or source.stem
    document = {
        "schema_version": "2.2",
        "project_type": "SINGLE_EPISODE" if len(episodes) == 1 else "SEASON_BATCH",
        "project_name": name,
        "media_root_hint": str(media_root),
        "output_subdirectory": settings.output_subdirectory,
        "content_type": content_type,
        "recap_language": language,
        "episodes": episodes,
    }
    output_dir = media_root / "recap_json"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{_safe_id(name)}_recap.json"
    temporary = output.with_suffix(".partial")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, output)
    try:
        import_recap_json(output, media_root)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return output
