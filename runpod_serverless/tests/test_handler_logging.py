"""Unit tests for handler logging helpers.

Run from the repo root:
    python3 runpod_serverless/tests/test_handler_logging.py
"""

import importlib
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


def _import_handler():
    for name in ("runpod", "boto3", "requests", "botocore", "botocore.client"):
        _stub_module(name)
    sys.modules["botocore.client"].Config = MagicMock()
    sys.modules["runpod"].serverless = MagicMock()
    sys.modules.pop("handler", None)
    if _RUNPOD_DIR not in sys.path:
        sys.path.insert(0, _RUNPOD_DIR)
    fake_proc = MagicMock(returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=fake_proc):
        return importlib.import_module("handler")


class HandlerLoggingTest(unittest.TestCase):

    def setUp(self):
        self.handler = _import_handler()

    def test_summarize_input_payload_redacts_base64(self):
        summary = self.handler._summarize_input_payload(
            {
                "source_image_base64": "abc123",
                "target_url": "https://example.com/target.mp4",
            }
        )
        self.assertEqual(summary["source_image_base64"], "<redacted len=6>")
        self.assertEqual(summary["target_url"], "https://example.com/target.mp4")

    def test_log_job_state_uses_info(self):
        job_logger = MagicMock()
        self.handler._log_job_state(job_logger, "PROCESSING")
        job_logger.info.assert_called_once_with("Job state=%s", "PROCESSING")

    def test_broadcast_error_context_uses_trace_level(self):
        job_logger = MagicMock()
        self.handler._broadcast_error_context(
            job_logger,
            "handler.py:test",
            "failure context",
            {"target_url": "https://example.com/target.mp4"},
            extra={"error": "boom"},
        )
        self.assertEqual(job_logger.log.call_args[0][0], self.handler.TRACE_LEVEL)

    def test_diagnose_source_image_reports_missing_file(self):
        diagnostics = self.handler._diagnose_source_image("/tmp/does-not-exist.jpg")
        self.assertFalse(diagnostics["file_exists"])
        self.assertIn("error", diagnostics)


if __name__ == "__main__":
    unittest.main()
