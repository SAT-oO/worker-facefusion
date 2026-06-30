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
    explicit = os.environ.get("FFMPEG_REAL_PATH")
    if explicit:
        return explicit
    if os.path.isfile("/usr/bin/ffmpeg"):
        return "/usr/bin/ffmpeg"
    return shutil.which("ffmpeg") or "/usr/bin/ffmpeg"


def ffmpeg_real_path() -> str:
    """System ffmpeg path, without the nvscope PATH wrapper."""
    return _ffmpeg_real_path()


def facefusion_subprocess_env() -> dict[str, str]:
    """Environment for facefusion subprocesses.

    Encoder discovery uses FFMPEG_ENCODER_PROBE (bare ffmpeg). Actual video
    encode/decode still goes through the PATH nvscope wrapper.
    """
    env = os.environ.copy()
    env["FFMPEG_ENCODER_PROBE"] = _ffmpeg_real_path()
    gpu_uuid = resolve_nvscope_gpu_uuid()
    if gpu_uuid:
        env["NVSCOPE_GPU_UUID"] = gpu_uuid
    return env


def resolve_nvscope_gpu_uuid() -> str | None:
    """Return the container-assigned GPU UUID for nvscope ioctl filtering."""
    explicit = os.environ.get("NVSCOPE_GPU_UUID", "").strip()
    if explicit:
        return explicit

    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None

    if proc.returncode != 0:
        return None

    for line in (proc.stdout or "").splitlines():
        value = line.strip()
        if value:
            return value
    return None


def warmup_nvscope(timeout: int = 30) -> tuple[bool, str]:
    """Prime nvscope GPU-id tables before the first NVENC ioctl alloc."""
    if not is_nvscope_installed():
        return False, "nvscope not installed"

    probe_ok, probe_summary = run_nvscope_probe(timeout=timeout)
    gpu_uuid = resolve_nvscope_gpu_uuid()
    if gpu_uuid and not os.environ.get("NVSCOPE_GPU_UUID"):
        os.environ["NVSCOPE_GPU_UUID"] = gpu_uuid

    summary = probe_summary
    if gpu_uuid:
        summary = f"{summary}; gpu_uuid={gpu_uuid}" if summary else f"gpu_uuid={gpu_uuid}"
    return probe_ok, summary


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


def probe_available_video_encoders(timeout: int = 30) -> list[str]:
    """List video encoders from the real ffmpeg binary (no nvscope LD_PRELOAD)."""
    ffmpeg = _ffmpeg_real_path()
    if not os.path.isfile(ffmpeg):
        return []

    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return []

    encoders: list[str] = []
    for line in (proc.stdout or "").lower().splitlines():
        if line.startswith(" v"):
            parts = line.split()
            if len(parts) >= 2:
                encoders.append(parts[1])
    return encoders


def probe_facefusion_video_encoders(timeout: int = 30) -> list[str]:
    """Video encoders FaceFusion argparse will accept (ffmpeg list ∩ FaceFusion choices)."""
    raw = set(probe_available_video_encoders(timeout))
    if not raw:
        return []

    try:
        from facefusion.choices import output_video_encoders
    except ImportError:
        return sorted(raw)

    return [name for name in output_video_encoders if name in raw]


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
