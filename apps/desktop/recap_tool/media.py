from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import threading
import ctypes
from pathlib import Path
from typing import Callable

from .gpu import bundled_binary, video_encode_args


LogCallback = Callable[[str], None]


class MediaError(RuntimeError):
    pass


class RenderCancelled(MediaError):
    pass


def find_binary(name: str) -> str:
    binary = bundled_binary(name)
    if not binary:
        raise MediaError(f"Không tìm thấy {name} trong runtime của Recap Studio.")
    return str(binary)


def run_command(
    args: list[str],
    *,
    cancel_event: threading.Event | None = None,
    log: LogCallback | None = None,
) -> subprocess.CompletedProcess[str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0
    )
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
    )
    lines: list[str] = []
    assert process.stdout is not None
    stream = process.stdout
    try:
        while True:
            line = stream.readline()
            if line:
                lines.append(line.rstrip())
                if log and ("Error" in line or "Invalid" in line):
                    log(line.rstrip())
            if cancel_event and cancel_event.is_set():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise RenderCancelled("Người dùng đã dừng xử lý.")
            if process.poll() is not None:
                break
    finally:
        stream.close()
    output = "\n".join(lines)
    if process.returncode != 0:
        tail = "\n".join(lines[-18:])
        raise MediaError(f"FFmpeg không thể xử lý tệp.\n{tail}")
    return subprocess.CompletedProcess(args, process.returncode, output, "")


def probe_media(path: str | Path) -> dict:
    media_path = Path(path)
    if not media_path.is_file():
        raise MediaError(f"Không tìm thấy tệp video: {media_path}")
    command = [
        find_binary("ffprobe"),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(media_path),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise MediaError(f"Không đọc được thông tin video: {completed.stderr.strip()}")
    payload = json.loads(completed.stdout)
    video = next((item for item in payload.get("streams", []) if item.get("codec_type") == "video"), None)
    if not video:
        raise MediaError(f"Tệp không có luồng hình ảnh: {media_path}")
    audio = next((item for item in payload.get("streams", []) if item.get("codec_type") == "audio"), None)
    duration = float(payload.get("format", {}).get("duration") or video.get("duration") or 0)
    rate_text = str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "25/1")
    try:
        numerator, denominator = rate_text.split("/", 1)
        fps = float(numerator) / max(float(denominator), 1.0)
    except (ValueError, ZeroDivisionError):
        fps = 25.0
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    return {
        "path": str(media_path.resolve()),
        "duration": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "fps_text": rate_text,
        "aspect_ratio": _aspect_ratio(width, height),
        "has_audio": audio is not None,
    }


def probe_duration(path: str | Path) -> float:
    media_path = Path(path)
    if not media_path.is_file():
        raise MediaError(f"Không tìm thấy tệp: {media_path}")
    command = [
        find_binary("ffprobe"),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(media_path),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise MediaError(f"Không đọc được thời lượng tệp: {completed.stderr.strip()}")
    try:
        return float(completed.stdout.strip().splitlines()[0])
    except (ValueError, IndexError) as exc:
        raise MediaError(f"Không xác định được thời lượng tệp: {media_path}") from exc


def _aspect_ratio(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return "Không xác định"
    divisor = math.gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def format_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def quality_args(quality: str) -> tuple[str, str]:
    mapping = {
        "standard": ("22", "veryfast"),
        "high": ("18", "medium"),
        "source": ("16", "slow"),
    }
    return mapping.get(quality, mapping["high"])


def cut_clip(
    source: Path,
    start_ms: float,
    end_ms: float,
    output: Path,
    *,
    quality: str,
    use_gpu: bool,
    cancel_event: threading.Event | None,
    log: LogCallback | None,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    encode_args, _status = video_encode_args(quality, use_gpu=use_gpu)
    duration = (float(end_ms) - float(start_ms)) / 1000.0
    args = [
        find_binary("ffmpeg"), "-y",
        "-ss", f"{float(start_ms) / 1000.0:.3f}",
        "-i", str(source),
        "-t", f"{duration:.3f}",
        "-map", "0:v:0",
        "-map", "0:a?",
        *encode_args,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-ar", "48000",
        "-ac", "2",
        "-movflags", "+faststart",
        str(output),
    ]
    run_command(args, cancel_event=cancel_event, log=log)
    return output


def extract_audio(
    source: Path,
    output: Path,
    *,
    cancel_event: threading.Event | None = None,
    log: LogCallback | None = None,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    args = [
        find_binary("ffmpeg"), "-y",
        "-i", str(source),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(output),
    ]
    run_command(args, cancel_event=cancel_event, log=log)
    return output


def extract_frame(
    source: Path,
    timestamp_seconds: float,
    output: Path,
    *,
    width: int = 960,
    cancel_event: threading.Event | None = None,
    log: LogCallback | None = None,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    args = [
        find_binary("ffmpeg"), "-y",
        "-ss", f"{timestamp_seconds:.3f}",
        "-i", str(source),
        "-frames:v", "1",
        "-vf", f"scale='min({max(320, int(width))},iw)':-2",
        "-q:v", "3",
        "-update", "1",
        str(output),
    ]
    run_command(args, cancel_event=cancel_event, log=log)
    return output


def create_analysis_proxy(
    source: Path,
    output: Path,
    *,
    height: int = 720,
    use_gpu: bool = True,
    cancel_event: threading.Event | None = None,
    log: LogCallback | None = None,
) -> Path:
    """Tạo bản nhẹ giữ nguyên timeline để gửi ChatGPT phân tích."""
    output.parent.mkdir(parents=True, exist_ok=True)
    encode_args, _status = video_encode_args("standard", use_gpu=use_gpu)
    args = [
        find_binary("ffmpeg"), "-y", "-i", str(source),
        "-vf", f"scale=-2:'min({max(360, int(height))},ih)'",
        *encode_args,
        "-maxrate", "1800k", "-bufsize", "3600k",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart", str(output),
    ]
    run_command(args, cancel_event=cancel_event, log=log)
    return output


def concat_videos(
    clips: list[Path],
    output: Path,
    *,
    cancel_event: threading.Event | None,
    log: LogCallback | None,
) -> Path:
    if not clips:
        raise MediaError("Không có clip để ghép.")
    output.parent.mkdir(parents=True, exist_ok=True)
    list_path = output.with_suffix(".concat.txt")
    rows = []
    for clip in clips:
        escaped = clip.resolve().as_posix().replace("'", "'\\''")
        rows.append(f"file '{escaped}'")
    list_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    args = [
        find_binary("ffmpeg"), "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_path),
        "-c", "copy",
        "-movflags", "+faststart",
        str(output),
    ]
    try:
        run_command(args, cancel_event=cancel_event, log=log)
    finally:
        list_path.unlink(missing_ok=True)
    return output


def calculate_video_fit(
    video_duration: float,
    voice_duration: float,
    minimum: float,
    maximum: float,
) -> tuple[float, float]:
    """Trả về (tốc độ hình, tỷ lệ cảnh cần giữ lại).

    Cảnh dài được cắt theo tỷ lệ trước khi ghép để mọi clip nguồn vẫn xuất
    hiện. Khi cảnh ngắn hơn giọng, tốc độ hình được chặn ở mức tối thiểu; phần
    thiếu còn lại sẽ được renderer bù bằng một frame hold ngắn. Cách này giúp
    sai số giữa ước tính trong JSON và thời lượng TTS thật không làm hỏng cả
    lượt render.
    """
    if voice_duration <= 0 or video_duration <= 0:
        raise MediaError("Không thể tính tốc độ vì thời lượng video hoặc giọng đọc bằng 0.")
    speed = video_duration / voice_duration
    if speed < minimum:
        return minimum, 1.0
    if speed > maximum:
        return maximum, (voice_duration * maximum) / video_duration
    return speed, 1.0


def calculate_video_speed(video_duration: float, voice_duration: float, minimum: float, maximum: float) -> float:
    return calculate_video_fit(video_duration, voice_duration, minimum, maximum)[0]


def calculate_video_padding(video_duration: float, voice_duration: float, speed: float) -> float:
    """Số giây cần giữ frame cuối sau khi đã đổi tốc độ hình."""
    if voice_duration <= 0 or video_duration <= 0 or speed <= 0:
        raise MediaError("Không thể tính phần bù vì thời lượng hoặc tốc độ bằng 0.")
    return max(0.0, voice_duration - (video_duration / speed))


def render_narration_segment(
    video_path: Path,
    voice_path: Path,
    output: Path,
    *,
    speed: float,
    quality: str,
    use_gpu: bool,
    cancel_event: threading.Event | None,
    log: LogCallback | None,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    encode_args, _status = video_encode_args(quality, use_gpu=use_gpu)
    source_info = probe_media(video_path)
    voice_duration = probe_duration(voice_path)
    video_duration = float(source_info["duration"])
    padding_duration = calculate_video_padding(video_duration, voice_duration, speed)
    video_filter = f"setpts=(PTS-STARTPTS)/{speed:.8f}"
    if padding_duration > 0.02:
        video_filter += f",tpad=stop_mode=clone:stop_duration={padding_duration:.8f}"
    filter_parts = [f"[0:v]{video_filter}[v]"]
    maps = ["-map", "[v]"]

    # Phân đoạn có lời recap chỉ phát giọng AI, tuyệt đối không trộn âm thanh phim.
    filter_parts.append("[1:a]aresample=48000,asetpts=PTS-STARTPTS,apad[narr]")
    maps += ["-map", "[narr]"]

    args = [
        find_binary("ffmpeg"), "-y",
        "-i", str(video_path),
        "-i", str(voice_path),
        "-filter_complex", ";".join(filter_parts),
        *maps,
        "-t", f"{voice_duration:.3f}",
        *encode_args,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-ar", "48000",
        "-ac", "2",
        "-movflags", "+faststart",
        str(output),
    ]
    run_command(args, cancel_event=cancel_event, log=log)
    return output


def burn_subtitles(
    source: Path,
    subtitles: Path,
    output: Path,
    *,
    quality: str,
    use_gpu: bool,
    cancel_event: threading.Event | None,
    log: LogCallback | None,
) -> Path:
    encode_args, _status = video_encode_args(quality, use_gpu=use_gpu)
    escaped = subtitles.resolve().as_posix().replace(":", "\\:").replace("'", "\\'")
    style = "FontName=Arial,FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=36"
    args = [
        find_binary("ffmpeg"), "-y",
        "-i", str(source),
        "-vf", f"subtitles=filename='{escaped}':force_style='{style}'",
        *encode_args,
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output),
    ]
    run_command(args, cancel_event=cancel_event, log=log)
    return output


def unique_output_path(path: Path) -> Path:
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = path.with_name(f"{path.stem}-v{index}{path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def open_path(path: Path) -> None:
    target = path.expanduser().resolve()
    if not target.exists():
        raise MediaError(f"Không tìm thấy đường dẫn để mở: {target}")
    if os.name == "nt":
        verb = "explore" if target.is_dir() else "open"
        try:
            result = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
                None, verb, str(target), None, None, 1
            )
        except (AttributeError, OSError) as exc:
            raise MediaError(f"Windows không thể mở đường dẫn: {target}") from exc
        if int(result) <= 32:
            raise MediaError(
                f"Windows Explorer không thể mở thư mục kết quả (mã lỗi {int(result)}): {target}"
            )
    elif shutil.which("open"):
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])
