"""Integration tests: NVENC fallback args must pass FaceFusion argparse.

Verifies handler fallback decisions use the same encoder set FaceFusion accepts.

Run from repo root:
    python3 runpod_serverless/tests/encoder_fallback/test_encoder_fallback_integration.py
"""

from __future__ import annotations

import importlib
import logging
import os
import shutil
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
_RUNPOD_DIR = os.path.join(_REPO_ROOT, "runpod_serverless")
for path in (_REPO_DIR := _RUNPOD_DIR, _REPO_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

import nvscope_compat  # noqa: E402


def _stub_runpod_deps() -> None:
    for name in ("runpod", "boto3", "requests", "botocore", "botocore.client"):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__getattr__ = lambda attr: MagicMock()
            sys.modules[name] = module
    sys.modules["botocore.client"].Config = MagicMock()
    sys.modules["runpod"].serverless = MagicMock()


class EncoderFallbackIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._ffmpeg = shutil.which("ffmpeg")
        if not cls._ffmpeg:
            raise unittest.SkipTest("ffmpeg not installed")
        os.environ["FFMPEG_REAL_PATH"] = cls._ffmpeg

    def _import_handler_nvenc_failed(self):
        _stub_runpod_deps()
        sys.modules.pop("handler", None)
        fake_proc = MagicMock(returncode=1, stdout="", stderr="nvenc probe stub")
        with patch("subprocess.run", return_value=fake_proc):
            handler = importlib.import_module("handler")
        handler.AVAILABLE_VIDEO_ENCODERS = nvscope_compat.probe_facefusion_video_encoders()
        if not handler.AVAILABLE_VIDEO_ENCODERS:
            handler.AVAILABLE_VIDEO_ENCODERS = ["libx264", "libx265", "h264_nvenc", "hevc_nvenc"]
        return handler

    def test_handler_and_facefusion_encoder_sets_match(self) -> None:
        env = nvscope_compat.facefusion_subprocess_env()
        with patch.dict(os.environ, env, clear=False):
            from facefusion.ffmpeg import get_available_encoder_set

            ff_video = get_available_encoder_set().get("video", [])
        handler_video = nvscope_compat.probe_facefusion_video_encoders()

        self.assertEqual(handler_video, ff_video)
        self.assertIn("libx264", handler_video)

    def test_fallback_libx264_passes_facefusion_argparse(self) -> None:
        handler = self._import_handler_nvenc_failed()
        job_logger = MagicMock()
        args = ["--output-video-encoder", "h264_nvenc", "--output-video-quality", "85"]

        with patch.object(handler, "_probe_nvenc", return_value=False):
            patched = handler._apply_nvenc_fallback(args, job_logger)

        self.assertEqual(patched[0:2], ["--output-video-encoder", "libx264"])

        env = nvscope_compat.facefusion_subprocess_env()
        with patch.dict(os.environ, env, clear=False):
            from facefusion import state_manager
            from facefusion.program import create_output_creation_program

            state_manager.init_item("config_path", os.devnull)
            prog = create_output_creation_program()
            parsed, _ = prog.parse_known_args(patched)
        self.assertEqual(parsed.output_video_encoder, "libx264")

    def test_fallback_hevc_to_libx265_passes_argparse(self) -> None:
        handler = self._import_handler_nvenc_failed()
        if "libx265" not in handler.AVAILABLE_VIDEO_ENCODERS:
            self.skipTest("libx265 not available in this ffmpeg build")

        with patch.object(handler, "_probe_nvenc", return_value=False):
            patched = handler._apply_nvenc_fallback(
                ["--output-video-encoder", "hevc_nvenc"],
                MagicMock(),
            )
        self.assertEqual(patched, ["--output-video-encoder", "libx265"])

        env = nvscope_compat.facefusion_subprocess_env()
        with patch.dict(os.environ, env, clear=False):
            from facefusion import state_manager
            from facefusion.ffmpeg import get_available_encoder_set

            state_manager.init_item("config_path", os.devnull)
            choices = get_available_encoder_set().get("video", [])
        self.assertIn("libx265", choices)

    def test_empty_facefusion_encoders_drops_flag_not_invalid_libx264(self) -> None:
        handler = self._import_handler_nvenc_failed()
        handler.AVAILABLE_VIDEO_ENCODERS = []
        job_logger = MagicMock()
        args = ["--output-video-encoder", "h264_nvenc", "--output-video-preset", "fast"]

        with patch.object(handler, "_probe_nvenc", return_value=False):
            patched = handler._apply_nvenc_fallback(args, job_logger)

        self.assertNotIn("--output-video-encoder", patched)
        self.assertNotIn("libx264", patched)
        self.assertEqual(patched, ["--output-video-preset", "fast"])


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main(verbosity=2)
