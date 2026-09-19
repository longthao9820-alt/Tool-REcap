from __future__ import annotations

import unittest
from unittest.mock import patch

from recap_tool.gpu import EncoderStatus, video_encode_args


class GpuTests(unittest.TestCase):
    def test_nvenc_arguments_are_used(self) -> None:
        status = EncoderStatus(True, "NVIDIA RTX", "h264_nvenc", "NVIDIA NVENC")
        with patch("recap_tool.gpu.detect_gpu_encoder", return_value=status):
            args, selected = video_encode_args("high", use_gpu=True)
        self.assertIn("h264_nvenc", args)
        self.assertEqual(selected.encoder, "h264_nvenc")

    def test_cpu_requires_explicit_gpu_false(self) -> None:
        with patch("recap_tool.gpu.detect_gpu_encoder"):
            args, selected = video_encode_args("high", use_gpu=False)
        self.assertIn("libx264", args)
        self.assertEqual(selected.encoder, "libx264")


if __name__ == "__main__":
    unittest.main()
