"""Unit tests for runpod_serverless/nvscope_compat.py.

Run from the repo root:
    python3 runpod_serverless/tests/test_nvscope_compat.py
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_RUNPOD_DIR = os.path.join(os.path.dirname(__file__), "..")
if _RUNPOD_DIR not in sys.path:
    sys.path.insert(0, _RUNPOD_DIR)

import nvscope_compat  # noqa: E402


class NvscopeCompatTest(unittest.TestCase):

    def test_is_nvscope_disabled_respects_env(self):
        with patch.dict(os.environ, {"NVSCOPE_DISABLED": "1"}, clear=False):
            self.assertTrue(nvscope_compat.is_nvscope_disabled())
        with patch.dict(os.environ, {"NVSCOPE_DISABLED": "0"}, clear=False):
            self.assertFalse(nvscope_compat.is_nvscope_disabled())

    def test_is_nvscope_installed_false_when_disabled(self):
        with patch.dict(os.environ, {"NVSCOPE_DISABLED": "1"}, clear=False):
            with patch.object(nvscope_compat.os.path, "isfile", return_value=True):
                with patch.object(nvscope_compat.os, "access", return_value=True):
                    self.assertFalse(nvscope_compat.is_nvscope_installed())

    def test_is_nvscope_installed_true_when_binaries_present(self):
        with patch.dict(os.environ, {"NVSCOPE_DISABLED": "0"}, clear=False):
            with patch.object(nvscope_compat.os.path, "isfile", return_value=True):
                with patch.object(nvscope_compat.os, "access", return_value=True):
                    self.assertTrue(nvscope_compat.is_nvscope_installed())

    def test_resolve_ffmpeg_executable_prefers_which(self):
        with patch.object(nvscope_compat.shutil, "which", return_value="/usr/local/bin/ffmpeg"):
            self.assertEqual(nvscope_compat.resolve_ffmpeg_executable(), "/usr/local/bin/ffmpeg")

    def test_resolve_ffmpeg_executable_falls_back_to_real_path(self):
        with patch.object(nvscope_compat.shutil, "which", return_value=None):
            with patch.dict(os.environ, {"FFMPEG_REAL_PATH": "/opt/ffmpeg"}, clear=False):
                self.assertEqual(nvscope_compat.resolve_ffmpeg_executable(), "/opt/ffmpeg")

    def test_describe_gpu_devices_lists_nodes(self):
        with patch.object(nvscope_compat.glob, "glob", return_value=["/dev/nvidia2", "/dev/nvidia7"]):
            self.assertEqual(nvscope_compat.describe_gpu_devices(), "nvidia2, nvidia7")

    def test_run_nvscope_probe_missing_binary(self):
        with patch.object(nvscope_compat.shutil, "which", return_value=None):
            ok, summary = nvscope_compat.run_nvscope_probe()
        self.assertFalse(ok)
        self.assertIn("not installed", summary)

    def test_run_nvscope_probe_success(self):
        fake_proc = MagicMock(returncode=0, stdout="line1\nprobe ok\n", stderr="")
        with patch.object(nvscope_compat.shutil, "which", return_value="/usr/local/bin/nvscope-probe"):
            with patch.object(nvscope_compat.subprocess, "run", return_value=fake_proc):
                ok, summary = nvscope_compat.run_nvscope_probe()
        self.assertTrue(ok)
        self.assertEqual(summary, "probe ok")

    def test_describe_nvscope_status_skips_probe_when_not_installed(self):
        with patch.object(nvscope_compat, "is_nvscope_installed", return_value=False):
            with patch.object(nvscope_compat, "describe_gpu_devices", return_value="nvidia3"):
                status = nvscope_compat.describe_nvscope_status()
        self.assertEqual(status["gpu_devices"], "nvidia3")
        self.assertNotIn("nvscope_probe_ok", status)


if __name__ == "__main__":
    unittest.main(verbosity=2)
