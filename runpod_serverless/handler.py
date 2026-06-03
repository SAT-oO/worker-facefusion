import base64
import json
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


def _models_dir():
    return os.path.join(_REPO_ROOT, ".assets", "models")


# #region startup self-check (debug)
def _startup_self_check():
    try:
        import cv2, numpy, onnx, onnxruntime, scipy
        print(f"[startup] python={sys.version.split()[0]}", flush=True)
        print(f"[startup] versions: cv2={cv2.__version__} numpy={numpy.__version__} "
              f"onnx={onnx.__version__} onnxruntime={onnxruntime.__version__} "
              f"scipy={scipy.__version__}", flush=True)
        print(f"[startup] ort providers: {onnxruntime.get_available_providers()}", flush=True)
        models_dir = _models_dir()
        if os.path.isdir(models_dir):
            files = sorted(os.listdir(models_dir))
            swapper = [f for f in files if "swap" in f.lower() or "hyperswap" in f.lower()]
            print(f"[startup] models_dir={models_dir} count={len(files)} swappers={swapper[:6]}", flush=True)
        else:
            print(f"[startup] models_dir MISSING: {models_dir}", flush=True)
        for k in ("R2_ACCOUNT_ID", "R2_BUCKET", "R2_PUBLIC_BASE_URL", "R2_ENDPOINT"):
            v = os.environ.get(k, "")
            print(f"[startup] env {k}={'<set>' if v else '<unset>'}", flush=True)
    except Exception as e:
        print(f"[startup] self-check error: {e}", flush=True)


_startup_self_check()
# #endregion


R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET = os.environ.get("R2_BUCKET", "")
R2_PUBLIC_BASE_URL = os.environ.get("R2_PUBLIC_BASE_URL", "").rstrip("/")

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
    if _is_r2_url(target_url):
        bucket, _ = _parse_r2_url(target_url)
        if bucket:
            return bucket
    return R2_BUCKET


def _debug_log(hypothesis_id, location, message, data, run_id="pre-fix"):
    # #region agent log
    try:
        from facefusion.agent_debug_log import agent_debug_log
        agent_debug_log(hypothesis_id, location, message, data, run_id=run_id)
    except Exception:
        pass
    # #endregion


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


def _read_subprocess_agent_debug():
    # #region agent log
    try:
        from facefusion.agent_debug_log import read_container_debug_lines
        lines = read_container_debug_lines()
        return [json.loads(line) for line in lines]
    except Exception:
        return []
    # #endregion


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


def handler(event):
    tmpdir = tempfile.mkdtemp(prefix="ff_")
    handler_started = time()
    timings = {}
    try:
        payload = event.get("input") or {}

        src_b64 = payload.get("source_image_base64")
        target_url = payload.get("target_url")
        if not src_b64:
            return {"error": "source_image_base64 is required"}
        if not target_url:
            return {"error": "target_url is required"}

        src_fmt = (payload.get("source_image_format") or "png").lower().lstrip(".")
        output_format = _ext_from_format(payload.get("output_format") or "mp4")
        processors = payload.get("processors") or ["face_swapper"]
        face_swapper_model = payload.get("face_swapper_model")
        extra_args = payload.get("extra_args") or []

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

        agent_debug_path = "/tmp/facefusion_agent_debug.ndjson"
        try:
            os.remove(agent_debug_path)
        except OSError:
            pass

        phase_started = time()
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=_REPO_ROOT)
        timings["facefusion_ms"] = _elapsed_ms(phase_started)
        if proc.returncode != 0 or not os.path.exists(output_path):
            yolo_diagnostics = None
            subprocess_agent_debug = _read_subprocess_agent_debug()
            if "loading model yoloface_8n failed" in (proc.stderr or "") or any(
                entry.get("location") == "inference_manager.py:create_inference_session:failed"
                for entry in subprocess_agent_debug
            ):
                _debug_log(
                    "H1",
                    "handler.py:headless-run-failure",
                    "detected yoloface model load failure in facefusion stderr",
                    {
                        "returncode": proc.returncode,
                        "stderr_tail": _tail(proc.stderr, 1200),
                    },
                )
                yolo_diagnostics = _diagnose_yolo_model_load()
                _debug_log(
                    "H2",
                    "handler.py:yolo-diagnostics",
                    "captured local yoloface load diagnostics",
                    yolo_diagnostics,
                )
            diagnostics = {}
            if yolo_diagnostics:
                diagnostics["yolo_model_load"] = yolo_diagnostics
            if subprocess_agent_debug:
                diagnostics["subprocess_agent_debug"] = subprocess_agent_debug
            timings["handler_total_ms"] = _elapsed_ms(handler_started)
            return {
                "error": f"headless-run failed (exit {proc.returncode})",
                "stderr": _tail(proc.stderr),
                "stdout": _tail(proc.stdout),
                "diagnostics": diagnostics,
                "timings": timings,
            }

        bucket = _resolve_bucket(target_url)
        if not bucket:
            timings["handler_total_ms"] = _elapsed_ms(handler_started)
            return {"error": "R2 bucket not configured", "timings": timings}

        key = f"outputs/{uuid.uuid4().hex}.{output_format}"
        phase_started = time()
        output_url = _upload_output(output_path, key, bucket)
        timings["upload_output_ms"] = _elapsed_ms(phase_started)
        timings["handler_total_ms"] = _elapsed_ms(handler_started)

        return {
            "output_url": output_url,
            "output_key": key,
            "bucket": bucket,
            "timings": timings,
        }
    except Exception as exc:
        timings["handler_total_ms"] = _elapsed_ms(handler_started)
        return {
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "timings": timings,
        }
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
