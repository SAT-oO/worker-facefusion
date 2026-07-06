"""Tests for the NVENC -> CPU encoder runtime fallback in handler.py.

handler.py runs a real ffmpeg NVENC probe at import time and imports worker-only
deps (runpod, boto3), so these tests stub those modules and fake the probe's
subprocess result before importing the handler. No GPU or worker deps required.

Run from the repo root:
    python3 runpod_serverless/tests/encoder_fallback/test_encoder_fallback.py
"""

import importlib
import logging
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

_RUNPOD_DIR = __file__.rsplit("/tests/", 1)[0]


def _stub_module(name: str) -> None:
    if name not in sys.modules:
        module = types.ModuleType(name)
        module.__getattr__ = lambda attr: MagicMock()
        sys.modules[name] = module


def _import_handler(probe_returncode: int):
    """Import a fresh handler module with a faked NVENC probe result."""
    for name in ("runpod", "boto3", "requests", "botocore", "botocore.client"):
        _stub_module(name)
    sys.modules["botocore.client"].Config = MagicMock()
    sys.modules["runpod"].serverless = MagicMock()

    sys.modules.pop("handler", None)
    if _RUNPOD_DIR not in sys.path:
        sys.path.insert(0, _RUNPOD_DIR)

    fake_proc = MagicMock(returncode=probe_returncode, stdout="", stderr="probe stub")
    with patch("nvscope_compat.warmup_nvscope", return_value=(False, "stub")):
        with patch("subprocess.run", return_value=fake_proc):
            handler = importlib.import_module("handler")
    handler.AVAILABLE_VIDEO_ENCODERS = ["libx264", "libx265", "h264_nvenc", "hevc_nvenc"]
    return handler


class EncoderFallbackTest(unittest.TestCase):

    def test_probe_failure_rewrites_nvenc_encoders(self):
        handler = _import_handler(probe_returncode=1)
        self.assertFalse(handler.NVENC_AVAILABLE)

        job_logger = MagicMock()
        args = ["--output-video-encoder", "h264_nvenc", "--output-video-quality", "85"]
        with patch.object(handler, "_probe_nvenc", return_value=False) as reprobe:
            patched = handler._apply_nvenc_fallback(args, job_logger)

        reprobe.assert_called_once()
        self.assertEqual(patched, ["--output-video-encoder", "libx264", "--output-video-quality", "85"])
        job_logger.warning.assert_called_once()
        self.assertIn("NVENC FALLBACK ACTIVE", job_logger.warning.call_args[0][0])

    def test_probe_failure_rewrites_hevc_nvenc(self):
        handler = _import_handler(probe_returncode=1)
        with patch.object(handler, "_probe_nvenc", return_value=False):
            patched = handler._apply_nvenc_fallback(["hevc_nvenc"], MagicMock())
        self.assertEqual(patched, ["libx265"])

    def test_probe_failure_leaves_non_nvenc_args_untouched(self):
        handler = _import_handler(probe_returncode=1)
        job_logger = MagicMock()
        args = ["--output-video-encoder", "libx264", "--output-video-preset", "medium"]
        with patch.object(handler, "_probe_nvenc", return_value=False) as reprobe:
            self.assertEqual(handler._apply_nvenc_fallback(args, job_logger), args)
        reprobe.assert_not_called()  # no nvenc args -> no re-probe needed
        job_logger.warning.assert_not_called()

    def test_reprobe_recovers_nvenc_after_failed_startup_probe(self):
        handler = _import_handler(probe_returncode=1)
        self.assertFalse(handler.NVENC_AVAILABLE)

        job_logger = MagicMock()
        args = ["--output-video-encoder", "h264_nvenc"]
        with patch.object(handler, "_probe_nvenc", return_value=True) as reprobe:
            patched = handler._apply_nvenc_fallback(args, job_logger)

        reprobe.assert_called_once()
        self.assertEqual(patched, args)
        self.assertTrue(handler.NVENC_AVAILABLE)
        job_logger.warning.assert_not_called()

        # After a successful per-job reprobe, later jobs use the cached result until invalidated.
        with patch.object(handler, "_probe_nvenc", return_value=False) as reprobe2:
            handler.invalidate_nvenc_probe()
            patched = handler._apply_nvenc_fallback(args, job_logger)
        reprobe2.assert_called_once()
        self.assertEqual(patched, ["--output-video-encoder", "libx264"])

    def test_probe_success_skips_reprobe_when_nvenc_cached(self):
        handler = _import_handler(probe_returncode=0)
        self.assertTrue(handler.NVENC_AVAILABLE)

        job_logger = MagicMock()
        args = ["--output-video-encoder", "h264_nvenc"]
        with patch.object(handler, "_probe_nvenc", return_value=True) as reprobe:
            self.assertEqual(handler._apply_nvenc_fallback(args, job_logger), args)
        reprobe.assert_not_called()
        job_logger.warning.assert_not_called()

    def test_cached_nvenc_fallbacks_after_invalidation(self):
        handler = _import_handler(probe_returncode=0)
        self.assertTrue(handler.NVENC_AVAILABLE)

        job_logger = MagicMock()
        args = ["--output-video-encoder", "h264_nvenc"]
        handler.invalidate_nvenc_probe()
        self.assertFalse(handler.NVENC_AVAILABLE)

        with patch.object(handler, "_probe_nvenc", return_value=False) as reprobe:
            patched = handler._apply_nvenc_fallback(args, job_logger)
        reprobe.assert_called_once()
        self.assertEqual(patched, ["--output-video-encoder", "libx264"])

    def test_probe_failure_drops_encoder_when_cpu_fallback_unavailable(self):
        handler = _import_handler(probe_returncode=1)
        handler.AVAILABLE_VIDEO_ENCODERS = []

        job_logger = MagicMock()
        args = ["--output-video-encoder", "h264_nvenc", "--output-video-quality", "85"]
        with patch.object(handler, "_probe_nvenc", return_value=False):
            patched = handler._apply_nvenc_fallback(args, job_logger)

        self.assertEqual(patched, ["--output-video-quality", "85"])
        self.assertTrue(any("dropping --output-video-encoder" in str(call) for call in job_logger.warning.call_args_list))


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main(verbosity=2)
