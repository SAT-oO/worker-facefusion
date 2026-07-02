"""Unit tests for handler source-quality stderr parsing."""

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


class SourceQualityParsingTest(unittest.TestCase):

    def setUp(self):
        self.handler = _import_handler()

    def test_parse_source_quality_stderr(self):
        stderr = (
            "[FACEFUSION.FACE_SWAPPER] source_face_quality_insufficient: "
            "landmarker_below_threshold, eyes_low_aspect_ratio!\n"
            '[FACEFUSION.FACE_SWAPPER] {"reasons":["eyes_low_aspect_ratio","landmarker_below_threshold"],'
            '"sources":[{"landmarker_score":0.41}]}\n'
        )
        parsed = self.handler._parse_source_quality_stderr(stderr)

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["error"], "source_face_quality_insufficient")
        self.assertIn("landmarker_below_threshold", parsed["reasons"])
        self.assertIn("metrics", parsed)


if __name__ == "__main__":
    unittest.main()
