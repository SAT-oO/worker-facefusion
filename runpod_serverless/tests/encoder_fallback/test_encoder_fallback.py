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
    with patch("subprocess.run", return_value=fake_proc):
        return importlib.import_module("handler")


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

        # Once recovered, subsequent jobs trust the cached value (no re-probe).
        with patch.object(handler, "_probe_nvenc", return_value=False) as reprobe2:
            self.assertEqual(handler._apply_nvenc_fallback(args, job_logger), args)
        reprobe2.assert_not_called()

    def test_probe_success_passes_nvenc_through(self):
        handler = _import_handler(probe_returncode=0)
        self.assertTrue(handler.NVENC_AVAILABLE)

        job_logger = MagicMock()
        args = ["--output-video-encoder", "h264_nvenc"]
        self.assertEqual(handler._apply_nvenc_fallback(args, job_logger), args)
        job_logger.warning.assert_not_called()


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main(verbosity=2)
