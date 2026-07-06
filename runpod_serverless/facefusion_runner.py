"""In-process FaceFusion headless-run for the RunPod worker.

Avoids per-job subprocess spawn and ONNX session reload on warm workers.
Set FF_SUBPROCESS=1 to fall back to the legacy subprocess path.
"""

from __future__ import annotations

import io
import logging
import os
import sys
from dataclasses import dataclass
from typing import Iterable

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RUNNER_INITIALIZED = False


@dataclass(frozen=True)
class HeadlessRunResult:
    returncode: int
    stdout: str
    stderr: str


class _LogCapture:
    def __init__(self) -> None:
        self._buffer = io.StringIO()
        self._handler = logging.StreamHandler(self._buffer)
        self._handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
        self._attached: list[logging.Logger] = []

    def install(self) -> None:
        for name in ("facefusion", "facefusion.*"):
            pass
        for logger_name in ("", "facefusion"):
            log = logging.getLogger(logger_name)
            log.addHandler(self._handler)
            self._attached.append(log)

    def remove(self) -> None:
        for log in self._attached:
            log.removeHandler(self._handler)
        self._attached.clear()

    @property
    def text(self) -> str:
        return self._buffer.getvalue()


def _ensure_repo_path() -> None:
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)


def _ensure_runner(jobs_path: str) -> None:
    global _RUNNER_INITIALIZED
    if _RUNNER_INITIALIZED:
        return

    _ensure_repo_path()

    from facefusion import logger, state_manager
    from facefusion.jobs import job_manager

    os.makedirs(jobs_path, exist_ok=True)
    if not job_manager.init_jobs(jobs_path):
        raise RuntimeError(f"failed to init facefusion jobs at {jobs_path}")

    state_manager.init_item("jobs_path", jobs_path)
    logger.init("warn")
    _RUNNER_INITIALIZED = True


def _build_argv(
    source_path: str,
    target_path: str,
    output_path: str,
    processors: Iterable[str],
    face_swapper_model: str | None,
    extra_args: list,
) -> list[str]:
    argv = [
        "facefusion.py",
        "headless-run",
        "--source-paths",
        source_path,
        "--target-path",
        target_path,
        "--output-path",
        output_path,
        "--processors",
        *processors,
        "--log-level",
        "warn",
    ]
    if face_swapper_model:
        argv.extend(["--face-swapper-model", face_swapper_model])
    if extra_args:
        argv.extend(str(arg) for arg in extra_args)
    return argv


def run_headless(
    source_path: str,
    target_path: str,
    output_path: str,
    processors: list[str],
    face_swapper_model: str | None,
    extra_args: list,
    *,
    jobs_path: str | None = None,
) -> HeadlessRunResult:
    """Run facefusion headless-run in the current process."""
    resolved_jobs_path = jobs_path or os.path.join(_REPO_ROOT, ".jobs", "runpod")
    _ensure_runner(resolved_jobs_path)

    from facefusion import state_manager
    from facefusion.args import apply_args
    from facefusion.core import process_headless
    from facefusion.program import create_program

    argv = _build_argv(
        source_path,
        target_path,
        output_path,
        processors,
        face_swapper_model,
        extra_args,
    )

    capture = _LogCapture()
    capture.install()
    try:
        program = create_program()
        args = vars(program.parse_args(argv))
        apply_args(args, state_manager.set_item)
        error_code = process_headless(args)
        log_text = capture.text
        return HeadlessRunResult(
            returncode=0 if error_code == 0 else int(error_code) or 1,
            stdout="",
            stderr=log_text,
        )
    except Exception as exc:
        log_text = capture.text
        if log_text and not log_text.endswith("\n"):
            log_text += "\n"
        log_text += f"{type(exc).__name__}: {exc}"
        return HeadlessRunResult(returncode=1, stdout="", stderr=log_text)
    finally:
        capture.remove()
