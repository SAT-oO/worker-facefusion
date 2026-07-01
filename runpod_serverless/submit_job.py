#!/usr/bin/env python3
"""Submit a FaceFusion job to RunPod Serverless (equivalent to POST /v2/{endpoint_id}/run)."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

RUNPOD_API = "https://api.runpod.ai/v2"
TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"})


def api_request(method: str, url: str, api_key: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"RunPod API {method} {url} failed ({exc.code}): {body}") from exc


def default_body() -> dict[str, Any]:
    # Production defaults: matches the "production" profile in tests/benchmark/config.json.
    return {
        "input": {
            "source_image_base64": "<base64>",
            "source_image_format": "jpg",
            "target_url": "https://<bucket>.<account>.r2.cloudflarestorage.com/templates/clip.mp4",
            "output_format": "mp4",
            "processors": ["face_swapper"],
            "face_swapper_model": "inswapper_128_fp16",
            "extra_args": [
                "--execution-providers", "cuda",
                "--execution-thread-count", "8",
                "--video-memory-strategy", "tolerant",
                "--face-detector-model", "retinaface",
                "--face-swapper-pixel-boost", "256x256",
                "--output-video-scale", "1.0",
                "--output-video-encoder", "h264_nvenc",
                "--output-video-preset", "fast",
                "--output-video-quality", "85",
                "--temp-frame-format", "png",
                "--face-mask-types", "box",
                "--face-selector-mode", "one",
                "--face-selector-order", "large-small",
            ],
        },
        "policy": {
            "executionTimeout": 360000,
            "ttl": 3600000,
            "lowPriority": False,
        },
    }


def load_body(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def build_body_from_args(args: argparse.Namespace) -> dict[str, Any]:
    body = default_body()
    inp = body["input"]

    if args.source_image:
        image_path = Path(args.source_image)
        raw = image_path.read_bytes()
        inp["source_image_base64"] = base64.b64encode(raw).decode("ascii")
        inp["source_image_format"] = args.source_image_format or image_path.suffix.lstrip(".") or "jpg"

    if args.target_url:
        inp["target_url"] = args.target_url

    if args.extra_args_json:
        inp["extra_args"] = json.loads(args.extra_args_json)

    return body


def submit(endpoint_id: str, api_key: str, body: dict[str, Any]) -> dict[str, Any]:
    return api_request("POST", f"{RUNPOD_API}/{endpoint_id}/run", api_key, body)


def poll_status(
    endpoint_id: str,
    api_key: str,
    job_id: str,
    *,
    timeout_seconds: int,
    poll_interval: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = api_request("GET", f"{RUNPOD_API}/{endpoint_id}/status/{job_id}", api_key)
        if result.get("status") in TERMINAL_STATUSES:
            return result
        time.sleep(poll_interval)
    raise TimeoutError(f"job {job_id} did not finish within {timeout_seconds}s")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Submit a RunPod serverless job (POST /run). Optional poll until terminal status.",
    )
    parser.add_argument(
        "--body",
        type=Path,
        help="JSON request body (same shape as README body.json)",
    )
    parser.add_argument("--source-image", help="Path to source face image; sets source_image_base64")
    parser.add_argument("--source-image-format", help="Override source_image_format (default: from file ext)")
    parser.add_argument("--target-url", help="Target video/image HTTPS URL")
    parser.add_argument("--extra-args-json", help='JSON array for extra_args, e.g. \'["--execution-providers","cuda"]\'')
    parser.add_argument(
        "--poll",
        action="store_true",
        help="Poll GET /status/{id} until COMPLETED, FAILED, CANCELLED, or TIMED_OUT",
    )
    parser.add_argument("--poll-interval", type=float, default=3.0, help="Seconds between status polls")
    parser.add_argument("--timeout", type=int, default=420, help="Poll timeout in seconds (default 420)")
    parser.add_argument("--endpoint-id", default=os.environ.get("ENDPOINT_ID"), help="RunPod endpoint ID")
    parser.add_argument("--api-key", default=os.environ.get("RUNPOD_API_KEY"), help="RunPod API key")
    args = parser.parse_args()

    if not args.api_key:
        print("RUNPOD_API_KEY is required (env or --api-key)", file=sys.stderr)
        return 1
    if not args.endpoint_id:
        print("ENDPOINT_ID is required (env or --endpoint-id)", file=sys.stderr)
        return 1

    if args.body:
        body = load_body(args.body)
    else:
        body = build_body_from_args(args)
        if body["input"].get("source_image_base64") == "<base64>" and not args.target_url:
            print("Provide --body, or --source-image and --target-url", file=sys.stderr)
            return 1

    run_result = submit(args.endpoint_id, args.api_key, body)
    print(json.dumps(run_result, indent=2, ensure_ascii=False))

    job_id = run_result.get("id")
    if not job_id:
        return 1

    if not args.poll:
        print(f"\njob_id={job_id}", file=sys.stderr)
        print("Poll: GET /status/{id} or re-run with --poll", file=sys.stderr)
        return 0

    status_result = poll_status(
        args.endpoint_id,
        args.api_key,
        job_id,
        timeout_seconds=args.timeout,
        poll_interval=args.poll_interval,
    )
    print(json.dumps(status_result, indent=2, ensure_ascii=False))

    output = status_result.get("output")
    if isinstance(output, dict) and output.get("output_url"):
        print(f"\noutput_url={output['output_url']}", file=sys.stderr)

    final = status_result.get("status")
    return 0 if final == "COMPLETED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
