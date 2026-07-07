"""Tests for in-process FaceFusion runner argv parsing."""

import os
import sys
import unittest
from unittest.mock import patch

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from runpod_serverless.facefusion_runner import _build_argv, _parse_program_args


class TestFacefusionRunnerArgv(unittest.TestCase):
    def test_build_argv_includes_prog_for_subprocess_parity(self) -> None:
        argv = _build_argv("/src.png", "/tgt.mp4", "/out.mp4", ["face_swapper"], None, [])
        self.assertEqual(argv[0], "facefusion.py")
        self.assertEqual(argv[1], "headless-run")

    def test_parse_program_args_accepts_argv_with_prog_name(self) -> None:
        from facefusion.program import create_program

        argv = _build_argv(
            "/src.png",
            "/tgt.mp4",
            "/out.mp4",
            ["face_swapper"],
            None,
            ["--output-video-encoder", "libx264", "--output-video-preset", "fast"],
        )
        args = _parse_program_args(create_program(), argv)
        self.assertEqual(args["command"], "headless-run")
        self.assertEqual(args["output_video_encoder"], "libx264")

    def test_parse_program_args_rejects_invalid_command_as_value_error(self) -> None:
        from facefusion.program import create_program

        with self.assertRaises(ValueError) as ctx:
            _parse_program_args(create_program(), ["facefusion.py", "not-a-command"])
        self.assertIn("invalid choice", str(ctx.exception).lower())

    @patch("facefusion.core.process_headless", return_value=0)
    @patch("runpod_serverless.facefusion_runner._ensure_runner")
    def test_run_headless_does_not_raise_system_exit_on_valid_argv(
        self,
        _ensure_runner,
        process_headless,
    ) -> None:
        from runpod_serverless import facefusion_runner

        result = facefusion_runner.run_headless(
            "/src.png",
            "/tgt.mp4",
            "/out.mp4",
            ["face_swapper"],
            None,
            ["--output-video-encoder", "libx264", "--output-video-preset", "fast"],
        )
        process_headless.assert_called_once()
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
