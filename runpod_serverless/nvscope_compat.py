"""RunPod GPU topology shim helpers (nvscope integration).

nvscope filters NVIDIA procfs/ioctl topology so NVENC/NVDEC see only the GPU
devices mounted in the container. See https://github.com/MadiatorLabs/nvscope
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess

_NVSCOPE_BIN = "/usr/local/bin/nvscope"
_NVSCOPE_LIB = "/usr/local/lib/nvscope/libnvscope.so"


def _ffmpeg_real_path() -> str:
    return os.environ.get("FFMPEG_REAL_PATH", "/usr/bin/ffmpeg")


def is_nvscope_disabled() -> bool:
    return os.environ.get("NVSCOPE_DISABLED", "0").strip() in {"1", "true", "yes"}


def is_nvscope_installed() -> bool:
    return (
        not is_nvscope_disabled()
        and os.path.isfile(_NVSCOPE_BIN)
        and os.access(_NVSCOPE_BIN, os.X_OK)
        and os.path.isfile(_NVSCOPE_LIB)
    )


def resolve_ffmpeg_executable() -> str:
    """Return the ffmpeg binary path (wrapper when nvscope is active)."""
    wrapped = shutil.which("ffmpeg")
    if wrapped:
        return wrapped
    return _ffmpeg_real_path()


def describe_gpu_devices() -> str:
    """Comma-separated list of mounted /dev/nvidiaN device nodes."""
    devices = sorted(os.path.basename(path) for path in glob.glob("/dev/nvidia[0-9]*"))
    return ", ".join(devices) if devices else "none"


def run_nvscope_probe(timeout: int = 30) -> tuple[bool, str]:
    """Run nvscope-probe when installed; return (ok, one-line summary)."""
    probe = shutil.which("nvscope-probe")
    if not probe:
        return False, "nvscope-probe not installed"

    try:
        proc = subprocess.run(
            [probe],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:
        return False, str(exc)

    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if not output:
        summary = f"exit={proc.returncode}, no output"
    else:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        summary = lines[-1]
        if len(summary) > 500:
            summary = summary[:497] + "..."
    return proc.returncode == 0, summary


def describe_nvscope_status() -> dict[str, str]:
    """Startup diagnostics for handler logging."""
    status = {
        "nvscope_disabled": str(is_nvscope_disabled()).lower(),
        "nvscope_installed": str(is_nvscope_installed()).lower(),
        "ffmpeg_path": resolve_ffmpeg_executable(),
        "gpu_devices": describe_gpu_devices(),
    }
    if is_nvscope_installed():
        probe_ok, probe_summary = run_nvscope_probe()
        status["nvscope_probe_ok"] = str(probe_ok).lower()
        status["nvscope_probe_summary"] = probe_summary
    return status
