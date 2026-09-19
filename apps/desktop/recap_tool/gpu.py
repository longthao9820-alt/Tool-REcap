from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
        if root.name == "release" and (root.parent / "runtime" / "ffmpeg" / "bin" / "ffmpeg.exe").is_file():
            return root.parent
        return root
    return Path(__file__).resolve().parents[3]


def bundled_binary(name: str) -> Path | None:
    suffix = ".exe" if sys.platform == "win32" else ""
    bundled = application_root() / "runtime" / "ffmpeg" / "bin" / f"{name}{suffix}"
    if bundled.is_file():
        return bundled
    external = shutil.which(name)
    return Path(external).resolve() if external else None


@dataclass(frozen=True)
class EncoderStatus:
    available: bool
    gpu_name: str
    encoder: str
    label: str
    reason: str = ""


_cached_status: EncoderStatus | None = None


def detect_gpu_encoder(*, refresh: bool = False) -> EncoderStatus:
    global _cached_status
    if _cached_status is not None and not refresh:
        return _cached_status
    ffmpeg = bundled_binary("ffmpeg")
    if not ffmpeg:
        _cached_status = EncoderStatus(False, "Không xác định", "", "Thiếu FFmpeg", "Không tìm thấy FFmpeg đi kèm.")
        return _cached_status
    encoders = _run_text([str(ffmpeg), "-hide_banner", "-encoders"])
    gpu_name = _gpu_name()
    candidates: list[tuple[str, str]] = []
    lowered = gpu_name.casefold()
    if "nvidia" in lowered:
        candidates.append(("h264_nvenc", "NVIDIA NVENC"))
    if "amd" in lowered or "radeon" in lowered:
        candidates.append(("h264_amf", "AMD AMF"))
    if "intel" in lowered:
        candidates.append(("h264_qsv", "Intel Quick Sync"))
    candidates.extend([("h264_nvenc", "NVIDIA NVENC"), ("h264_amf", "AMD AMF"), ("h264_qsv", "Intel Quick Sync")])
    checked: set[str] = set()
    for encoder, label in candidates:
        if encoder in checked or encoder not in encoders:
            continue
        checked.add(encoder)
        if _test_encoder(ffmpeg, encoder):
            _cached_status = EncoderStatus(True, gpu_name, encoder, label)
            return _cached_status
    _cached_status = EncoderStatus(False, gpu_name, "libx264", "CPU", "Không có GPU encoder H.264 hoạt động.")
    return _cached_status


def video_encode_args(quality: str, *, use_gpu: bool = True) -> tuple[list[str], EncoderStatus]:
    status = detect_gpu_encoder()
    if use_gpu and status.available:
        if status.encoder == "h264_nvenc":
            cq, preset = {"standard": (23, "p4"), "high": (19, "p5"), "source": (17, "p6")}.get(quality, (19, "p5"))
            return ["-c:v", status.encoder, "-preset", preset, "-tune", "hq", "-rc", "vbr", "-cq", str(cq), "-b:v", "0"], status
        if status.encoder == "h264_amf":
            qp = {"standard": "23", "high": "19", "source": "17"}.get(quality, "19")
            return ["-c:v", status.encoder, "-quality", "quality", "-rc", "cqp", "-qp_i", qp, "-qp_p", qp], status
        if status.encoder == "h264_qsv":
            quality_value = {"standard": "23", "high": "19", "source": "17"}.get(quality, "19")
            return ["-c:v", status.encoder, "-preset", "medium", "-global_quality", quality_value], status
    crf, preset = {"standard": (22, "veryfast"), "high": (18, "medium"), "source": (16, "slow")}.get(quality, (18, "medium"))
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)], EncoderStatus(True, "CPU", "libx264", "CPU")


def _test_encoder(ffmpeg: Path, encoder: str) -> bool:
    command = [
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=640x360:d=0.15:r=25",
        "-frames:v", "2", "-c:v", encoder, "-f", "null", "NUL" if sys.platform == "win32" else "/dev/null",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _gpu_name() -> str:
    nvidia = shutil.which("nvidia-smi")
    if nvidia:
        text = _run_text([nvidia, "--query-gpu=name", "--format=csv,noheader"])
        if text.strip():
            return text.strip().splitlines()[0]
    return "GPU hệ thống"


def _run_text(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (result.stdout or "") + (result.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""
