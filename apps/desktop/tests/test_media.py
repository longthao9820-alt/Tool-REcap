from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from recap_tool.gpu import EncoderStatus
from recap_tool.media import (
    MediaError,
    calculate_video_fit,
    calculate_video_padding,
    calculate_video_speed,
    open_path,
    probe_duration,
    probe_media,
    render_narration_segment,
)


class MediaTests(unittest.TestCase):
    def test_speed_is_calculated_from_video_and_voice(self) -> None:
        self.assertAlmostEqual(calculate_video_speed(11.0, 10.0, 0.85, 1.15), 1.1)

    def test_video_too_short_for_voice_is_clamped_for_frame_hold(self) -> None:
        self.assertAlmostEqual(calculate_video_speed(5.0, 10.0, 0.85, 1.15), 0.85)
        self.assertAlmostEqual(calculate_video_padding(5.0, 10.0, 0.85), 10.0 - (5.0 / 0.85))

    def test_video_longer_than_voice_is_capped_for_tail_trim(self) -> None:
        self.assertAlmostEqual(calculate_video_speed(20.0, 10.0, 0.85, 1.15), 1.15)

    def test_video_longer_than_voice_is_trimmed_proportionally(self) -> None:
        speed, retain_ratio = calculate_video_fit(20.0, 10.0, 0.85, 1.15)
        self.assertAlmostEqual(speed, 1.15)
        self.assertAlmostEqual(retain_ratio, 0.575)

    def test_voice_longer_slows_video(self) -> None:
        self.assertAlmostEqual(calculate_video_speed(9.0, 10.0, 0.85, 1.15), 0.9)

    def test_default_fallback_handles_real_world_short_clip(self) -> None:
        self.assertAlmostEqual(calculate_video_speed(11.49, 14.42, 0.75, 1.15), 11.49 / 14.42)

    def test_probe_duration_hides_ffprobe_window(self) -> None:
        completed = MagicMock(returncode=0, stdout="3.25\n", stderr="")
        with patch("recap_tool.media.Path.is_file", return_value=True), patch(
            "recap_tool.media.find_binary", return_value="ffprobe.exe"
        ), patch("recap_tool.media.subprocess.run", return_value=completed) as run:
            self.assertEqual(probe_duration(Path("voice.wav")), 3.25)
        self.assertEqual(
            run.call_args.kwargs["creationflags"],
            getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0),
        )

    def test_probe_media_hides_ffprobe_window(self) -> None:
        completed = MagicMock(
            returncode=0,
            stdout='{"streams":[{"codec_type":"video","width":1920,"height":1080,"avg_frame_rate":"30/1"}],"format":{"duration":"5"}}',
            stderr="",
        )
        with patch("recap_tool.media.Path.is_file", return_value=True), patch(
            "recap_tool.media.find_binary", return_value="ffprobe.exe"
        ), patch("recap_tool.media.subprocess.run", return_value=completed) as run:
            self.assertEqual(probe_media(Path("video.mp4"))["duration"], 5.0)
        self.assertEqual(
            run.call_args.kwargs["creationflags"],
            getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0),
        )

    def test_open_path_uses_windows_shell_and_checks_result(self) -> None:
        shell = MagicMock()
        shell.ShellExecuteW.return_value = 33
        with patch("recap_tool.media.Path.exists", return_value=True), patch(
            "recap_tool.media.Path.is_dir", return_value=True
        ), patch("recap_tool.media.ctypes.windll.shell32", shell):
            open_path(Path("results"))
        self.assertEqual(shell.ShellExecuteW.call_args.args[1], "explore")

    def test_open_path_reports_windows_shell_failure(self) -> None:
        shell = MagicMock()
        shell.ShellExecuteW.return_value = 31
        with patch("recap_tool.media.Path.exists", return_value=True), patch(
            "recap_tool.media.Path.is_dir", return_value=True
        ), patch("recap_tool.media.ctypes.windll.shell32", shell), self.assertRaises(MediaError):
            open_path(Path("results"))

    def test_narration_never_mixes_movie_audio(self) -> None:
        captured = []
        encoder = EncoderStatus(True, "NVIDIA", "h264_nvenc", "NVENC")
        with patch("recap_tool.media.probe_media", return_value={"has_audio": True, "duration": 3.0}), patch(
            "recap_tool.media.probe_duration", return_value=3.0
        ), patch("recap_tool.media.video_encode_args", return_value=(["-c:v", "h264_nvenc"], encoder)), patch(
            "recap_tool.media.run_command", side_effect=lambda args, **_kwargs: captured.append(args)
        ):
            render_narration_segment(
                Path("video.mp4"), Path("voice.wav"), Path("out.mp4"),
                speed=1.0, quality="high", use_gpu=True, cancel_event=None, log=None,
            )
        command = " ".join(captured[0])
        self.assertNotIn("amix", command)
        self.assertNotIn("[0:a]", command)
        self.assertIn("[narr]", command)
        self.assertIn("PTS-STARTPTS", command)

    def test_short_visual_is_padded_instead_of_rejected(self) -> None:
        captured = []
        encoder = EncoderStatus(True, "NVIDIA", "h264_nvenc", "NVENC")
        with patch("recap_tool.media.probe_media", return_value={"has_audio": True, "duration": 2.0}), patch(
            "recap_tool.media.probe_duration", return_value=3.0
        ), patch("recap_tool.media.video_encode_args", return_value=(["-c:v", "h264_nvenc"], encoder)), patch(
            "recap_tool.media.run_command", side_effect=lambda args, **_kwargs: captured.append(args)
        ):
            render_narration_segment(
                Path("video.mp4"), Path("voice.wav"), Path("out.mp4"),
                speed=0.85, quality="high", use_gpu=True, cancel_event=None, log=None,
            )
        command = " ".join(captured[0])
        self.assertIn("tpad=stop_mode=clone", command)
        self.assertIn("-t 3.000", command)



if __name__ == "__main__":
    unittest.main()
