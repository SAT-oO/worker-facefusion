import base64
import json
import logging
import os
import subprocess
import sys
import tempfile
import traceback
import uuid
from time import time
from urllib.parse import urlparse

import boto3
import requests
from botocore.client import Config

import runpod

_RUNPOD_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_RUNPOD_DIR)
_LOGGER_NAME = "runpod_worker"


def _models_dir():
    return os.path.join(_REPO_ROOT, ".assets", "models")


def _setup_logger(log_level: int = logging.INFO) -> logging.Logger:
    log_format = logging.Formatter(
        "%(asctime)s - %(levelname)s - [Request: %(request_id)s] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    worker_logger = logging.getLogger(_LOGGER_NAME)
    worker_logger.setLevel(log_level)
    if not worker_logger.handlers:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(log_format)
        worker_logger.addHandler(console_handler)
    return worker_logger


def _job_logger(job_id: str) -> logging.LoggerAdapter:
    return logging.LoggerAdapter(
        logging.getLogger(_LOGGER_NAME),
        {"request_id": job_id or "unknown"},
    )


def _log_debug_event(
    job_logger: logging.LoggerAdapter,
    level: int,
    location: str,
    message: str,
    data: dict | None = None,
) -> None:
    payload = json.dumps(data or {}, default=str, ensure_ascii=False)
    job_logger.log(level, "%s | %s | data=%s", location, message, payload)


logger = logging.LoggerAdapter(_setup_logger(), {"request_id": "startup"})


def _startup_self_check() -> None:
    try:
        import cv2
        import numpy
        import onnx
        import onnxruntime
        import scipy

        logger.info("python=%s", sys.version.split()[0])
        logger.info(
            "versions: cv2=%s numpy=%s onnx=%s onnxruntime=%s scipy=%s",
            cv2.__version__,
            numpy.__version__,
            onnx.__version__,
            onnxruntime.__version__,
            scipy.__version__,
        )
        logger.info("ort providers: %s", onnxruntime.get_available_providers())
        models_dir = _models_dir()
        if os.path.isdir(models_dir):
            files = sorted(os.listdir(models_dir))
            swapper = [f for f in files if "swap" in f.lower() or "hyperswap" in f.lower()]
            logger.info(
                "models_dir=%s count=%s swappers=%s",
                models_dir,
                len(files),
                swapper[:6],
            )
        else:
            logger.warning("models_dir MISSING: %s", models_dir)
        for key in ("R2_ACCOUNT_ID", "R2_BUCKET", "R2_PUBLIC_BASE_URL", "R2_ENDPOINT"):
            value = os.environ.get(key, "")
            logger.info("env %s=%s", key, "<set>" if value else "<unset>")
    except Exception as exc:
        logger.error("startup self-check error: %s", exc, exc_info=True)


def _probe_nvenc() -> bool:
    """One-frame h264_nvenc encode to verify NVENC actually works on this worker."""
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc=duration=0.1:size=256x256:rate=25",
                "-frames:v", "1", "-c:v", "h264_nvenc", "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            logger.info("nvenc probe: OK")
            return True
        logger.warning("nvenc probe: FAILED, will fall back to CPU encoders | stderr=%s", (proc.stderr or "")[-500:])
    except Exception as exc:
        logger.warning("nvenc probe: error (%s), will fall back to CPU encoders", exc)
    return False


_NVENC_ENCODER_FALLBACKS = {
    "h264_nvenc": "libx264",
    "hevc_nvenc": "libx265",
}


def _apply_nvenc_fallback(extra_args: list, job_logger) -> list:
    """Replace NVENC encoders with CPU equivalents when NVENC is unavailable.

    A failed startup probe is re-checked once per job (the boot-time probe can
    race GPU plumbing on some hosts); a successful probe is trusted for the
    worker's lifetime.
    """
    global NVENC_AVAILABLE
    if NVENC_AVAILABLE:
        return extra_args
    if any(str(arg) in _NVENC_ENCODER_FALLBACKS for arg in extra_args):
        job_logger.info("nvenc marked unavailable; re-probing before applying fallback")
        if _probe_nvenc():
            NVENC_AVAILABLE = True
            job_logger.info("nvenc re-probe: OK; keeping NVENC encoder")
            return extra_args
    patched = []
    replacements = []
    for arg in extra_args:
        replacement = _NVENC_ENCODER_FALLBACKS.get(str(arg))
        if replacement:
            replacements.append(f"{arg} -> {replacement}")
            patched.append(replacement)
        else:
            patched.append(arg)
    if replacements:
        job_logger.warning(
            "NVENC FALLBACK ACTIVE: nvenc probe failed (startup and re-probe); encoding on CPU | %s",
            ", ".join(replacements),
        )
    return patched


logger.info("Logger initialized. Ready to process jobs.")
_startup_self_check()
NVENC_AVAILABLE = _probe_nvenc()


R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET = os.environ.get("R2_BUCKET", "")
R2_PUBLIC_BASE_URL = os.environ.get("R2_PUBLIC_BASE_URL", "").rstrip("/")
R2_OUTPUT_PREFIX = os.environ.get("R2_OUTPUT_PREFIX", "outputs").strip("/")

R2_ENDPOINT = os.environ.get("R2_ENDPOINT") or (
    f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com" if R2_ACCOUNT_ID else ""
)


def _r2_client():
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def _is_r2_url(url):
    if not url:
        return False
    if ".r2.cloudflarestorage.com" in url:
        return True
    if R2_PUBLIC_BASE_URL and url.startswith(R2_PUBLIC_BASE_URL):
        return True
    return False


def _parse_r2_url(url):
    if R2_PUBLIC_BASE_URL and url.startswith(R2_PUBLIC_BASE_URL):
        key = url[len(R2_PUBLIC_BASE_URL):].lstrip("/")
        return R2_BUCKET, key
    parsed = urlparse(url)
    host_parts = parsed.netloc.split(".")
    path = parsed.path.lstrip("/")
    if host_parts and host_parts[0] != R2_ACCOUNT_ID and len(host_parts) >= 5:
        bucket = host_parts[0]
        key = path
    else:
        segments = path.split("/", 1)
        bucket = segments[0] if segments else R2_BUCKET
        key = segments[1] if len(segments) > 1 else ""
    return bucket, key


def _download_target(url, dest_path):
    # SECURITY: target_url is caller-controlled. Without an allowlist (R2 domain /
    # R2_PUBLIC_BASE_URL only) and private-IP blocking, a job can trigger SSRF or
    # use worker R2 credentials against arbitrary buckets. Mitigate at the edge
    # (reverse proxy / API gateway) or add validation here before production exposure.
    if _is_r2_url(url):
        bucket, key = _parse_r2_url(url)
        try:
            _r2_client().download_file(bucket, key, dest_path)
            return
        except Exception:
            pass
    with requests.get(url, stream=True, timeout=300) as resp:
        resp.raise_for_status()
        with open(dest_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                if chunk:
                    fh.write(chunk)


def _upload_output(local_path, key, bucket):
    client = _r2_client()
    client.upload_file(local_path, bucket, key)
    if R2_PUBLIC_BASE_URL:
        return f"{R2_PUBLIC_BASE_URL}/{key}"
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=86400,
    )


def _ext_from_format(fmt):
    return fmt.lower().lstrip(".") or "mp4"


def _tail(text, limit=4000):
    if not text:
        return ""
    return text[-limit:]


def _resolve_bucket(target_url):
    # SECURITY: output is uploaded to the bucket inferred from target_url when parseable.
    # A crafted target_url can direct writes to another bucket the R2 token can access.
    # Scope the token to one bucket/prefix, or always upload to R2_BUCKET; gate untrusted
    # callers behind a reverse proxy or other perimeter controls.
    if _is_r2_url(target_url):
        bucket, _ = _parse_r2_url(target_url)
        if bucket:
            return bucket
    return R2_BUCKET


def _snapshot_gpu_memory():
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.free,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _diagnose_yolo_model_load():
    model_path = os.path.join(_models_dir(), "yoloface_8n.onnx")
    result = {
        "model_path": model_path,
        "model_exists": os.path.isfile(model_path),
        "model_size": os.path.getsize(model_path) if os.path.isfile(model_path) else -1,
        "gpu_mem_after_subprocess": _snapshot_gpu_memory(),
    }
    try:
        import onnxruntime as ort
        from facefusion.common_helper import get_first
        from facefusion.execution import create_inference_providers, get_available_execution_providers

        providers = ort.get_available_providers()
        result["ort_version"] = ort.__version__
        result["ort_available_providers"] = providers
        available_execution_providers = get_available_execution_providers()
        result["facefusion_available_execution_providers"] = available_execution_providers
        try:
            ort.InferenceSession(model_path, providers=providers)
            result["session_create_ok_all_ort_providers"] = True
        except Exception as exc:
            result["session_create_ok_all_ort_providers"] = False
            result["all_ort_providers_exception"] = f"{type(exc).__name__}: {exc}"

        provider_sets = {
            "default": [get_first(available_execution_providers)] if available_execution_providers else ["cpu"],
            "cuda_cpu": ["cuda", "cpu"],
        }
        for label, execution_providers in provider_sets.items():
            try:
                inference_providers = create_inference_providers(0, execution_providers)
                ort.InferenceSession(model_path, providers=inference_providers)
                result[f"session_create_ok_{label}"] = True
            except Exception as exc:
                result[f"session_create_ok_{label}"] = False
                result[f"exception_{label}"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        result["diagnostic_error"] = f"{type(exc).__name__}: {exc}"
    return result


def _elapsed_ms(start_time):
    return int((time() - start_time) * 1000)


def _log_job_completion(
    job_logger: logging.LoggerAdapter,
    status: str,
    timings: dict,
    *,
    error: str | None = None,
    output_url: str | None = None,
) -> None:
    summary = {
        "status": status,
        "timings": timings,
        "error": error,
        "output_url": output_url,
    }
    message = "Job finished | status=%s | handler_total_ms=%s"
    args: list = [status, timings.get("handler_total_ms")]
    if status == "COMPLETED":
        job_logger.info(message, *args)
        if output_url:
            job_logger.info("Job output_url=%s", output_url)
    else:
        job_logger.error(message, *args)
        if error:
            job_logger.error("Job error: %s", error)
    job_logger.debug("Job completion detail: %s", json.dumps(summary, default=str))


def handler(event):
    job_id = event.get("id", "unknown")
    job_logger = _job_logger(job_id)
    job_logger.info("Job received")

    tmpdir = tempfile.mkdtemp(prefix="ff_")
    handler_started = time()
    timings: dict = {}
    try:
        payload = event.get("input") or {}

        src_b64 = payload.get("source_image_base64")
        target_url = payload.get("target_url")
        if not src_b64:
            timings["handler_total_ms"] = _elapsed_ms(handler_started)
            result = {"error": "source_image_base64 is required", "timings": timings}
            _log_job_completion(job_logger, "FAILED", timings, error=result["error"])
            return result
        if not target_url:
            timings["handler_total_ms"] = _elapsed_ms(handler_started)
            result = {"error": "target_url is required", "timings": timings}
            _log_job_completion(job_logger, "FAILED", timings, error=result["error"])
            return result

        src_fmt = (payload.get("source_image_format") or "png").lower().lstrip(".")
        output_format = _ext_from_format(payload.get("output_format") or "mp4")
        processors = payload.get("processors") or ["face_swapper"]
        face_swapper_model = payload.get("face_swapper_model")
        extra_args = [str(a) for a in (payload.get("extra_args") or [])]
        patched_args = _apply_nvenc_fallback(extra_args, job_logger)
        encoder_info = {
            "nvenc_available": NVENC_AVAILABLE,
            "fallback_applied": patched_args != extra_args,
        }
        extra_args = patched_args

        job_logger.info(
            "Job submitted | processors=%s face_swapper_model=%s output_format=%s extra_args_count=%s",
            processors,
            face_swapper_model,
            output_format,
            len(extra_args),
        )

        phase_started = time()
        source_path = os.path.join(tmpdir, f"source.{src_fmt}")
        with open(source_path, "wb") as fh:
            fh.write(base64.b64decode(src_b64))
        timings["decode_source_ms"] = _elapsed_ms(phase_started)

        target_ext = os.path.splitext(urlparse(target_url).path)[1] or ".mp4"
        target_path = os.path.join(tmpdir, f"target{target_ext}")
        phase_started = time()
        _download_target(target_url, target_path)
        timings["download_target_ms"] = _elapsed_ms(phase_started)
        job_logger.info(
            "Target ready | download_target_ms=%s target_path=%s",
            timings["download_target_ms"],
            os.path.basename(target_path),
        )

        output_path = os.path.join(tmpdir, f"output.{output_format}")

        cmd = [
            sys.executable,
            os.path.join(_REPO_ROOT, "facefusion.py"),
            "headless-run",
            "--source-paths", source_path,
            "--target-path", target_path,
            "--output-path", output_path,
            "--processors", *processors,
        ]
        if face_swapper_model:
            cmd += ["--face-swapper-model", face_swapper_model]
        if extra_args:
            cmd += [str(a) for a in extra_args]

        job_logger.info("Starting facefusion headless-run")
        phase_started = time()
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=_REPO_ROOT)
        timings["facefusion_ms"] = _elapsed_ms(phase_started)
        job_logger.info("Facefusion finished | facefusion_ms=%s returncode=%s", timings["facefusion_ms"], proc.returncode)

        if proc.returncode != 0 or not os.path.exists(output_path):
            yolo_diagnostics = None
            if "loading model yoloface_8n failed" in (proc.stderr or ""):
                _log_debug_event(
                    job_logger,
                    logging.ERROR,
                    "handler.py:headless-run-failure",
                    "detected yoloface model load failure in facefusion stderr",
                    {
                        "returncode": proc.returncode,
                        "stderr_tail": _tail(proc.stderr, 1200),
                    },
                )
                yolo_diagnostics = _diagnose_yolo_model_load()
                _log_debug_event(
                    job_logger,
                    logging.ERROR,
                    "handler.py:yolo-diagnostics",
                    "captured local yoloface load diagnostics",
                    yolo_diagnostics,
                )
            diagnostics = {}
            if yolo_diagnostics:
                diagnostics["yolo_model_load"] = yolo_diagnostics
            timings["handler_total_ms"] = _elapsed_ms(handler_started)
            result = {
                "error": f"headless-run failed (exit {proc.returncode})",
                "stderr": _tail(proc.stderr),
                "stdout": _tail(proc.stdout),
                "diagnostics": diagnostics,
                "timings": timings,
            }
            if proc.stderr:
                job_logger.error("Facefusion stderr tail: %s", _tail(proc.stderr, 1200))
            _log_job_completion(job_logger, "FAILED", timings, error=result["error"])
            return result

        bucket = _resolve_bucket(target_url)
        if not bucket:
            timings["handler_total_ms"] = _elapsed_ms(handler_started)
            result = {"error": "R2 bucket not configured", "timings": timings}
            _log_job_completion(job_logger, "FAILED", timings, error=result["error"])
            return result

        key = f"{R2_OUTPUT_PREFIX}/{uuid.uuid4().hex}.{output_format}"
        phase_started = time()
        output_url = _upload_output(output_path, key, bucket)
        timings["upload_output_ms"] = _elapsed_ms(phase_started)
        timings["handler_total_ms"] = _elapsed_ms(handler_started)

        result = {
            "output_url": output_url,
            "output_key": key,
            "bucket": bucket,
            "encoder": encoder_info,
            "timings": timings,
        }
        _log_job_completion(job_logger, "COMPLETED", timings, output_url=output_url)
        return result
    except Exception as exc:
        timings["handler_total_ms"] = _elapsed_ms(handler_started)
        job_logger.error("Job failed with unexpected exception", exc_info=True)
        result = {
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "timings": timings,
        }
        _log_job_completion(job_logger, "FAILED", timings, error=str(exc))
        return result
    finally:
        try:
            for name in os.listdir(tmpdir):
                try:
                    os.remove(os.path.join(tmpdir, name))
                except OSError:
                    pass
            os.rmdir(tmpdir)
        except OSError:
            pass


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
