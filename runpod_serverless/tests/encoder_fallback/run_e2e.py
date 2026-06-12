#!/usr/bin/env python3
"""End-to-end check of the NVENC fallback against a live RunPod endpoint.

Reuses the benchmark profiles (config.json + fixtures/source.b64 + target URLs),
so it submits the exact same job shape as the benches. Then verifies from the
web response alone:
  1. job COMPLETED with an output_url
  2. output.encoder reports whether NVENC was available and if the
     libx264 fallback was applied (handler includes this in the result)
  3. (optional, needs ffprobe) downloads the output and confirms the
     actual codec matches what the handler reported

Usage:
    export RUNPOD_API_KEY=... ENDPOINT_ID=...
    python3 runpod_serverless/tests/encoder_fallback/run_e2e.py            # production profile, 60s target
    python3 runpod_serverless/tests/encoder_fallback/run_e2e.py --profile fast_nvenc --target 120s

Exit codes: 0 = pass, 1 = bad args/config, 2 = job failed, 3 = verification failed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parents[1]
_RUNPOD_DIR = _TESTS_DIR.parent
_BENCHMARK_DIR = _TESTS_DIR / "benchmark"
for path in (str(_RUNPOD_DIR), str(_BENCHMARK_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from benchmark_common import BENCHMARK_DIR, load_run_context
from submit_job import poll_status, submit

DEFAULT_PROFILE = "production"
DEFAULT_TARGET = "60s"


def _probe_output_codec(output_url: str) -> str | None:
    if not shutil.which("ffprobe"):
        print("ffprobe not found locally; skipping codec verification")
        return None
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        # Cloudflare bot protection 403s the default Python-urllib user agent.
        req = urllib.request.Request(output_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp_path, "wb") as fh:
            shutil.copyfileobj(resp, fh)
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=codec_name", "-of", "csv=p=0", tmp_path,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return proc.stdout.strip() or None
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="E2E NVENC fallback check (uses benchmark profiles)")
    parser.add_argument("--config", default=str(BENCHMARK_DIR / "config.json"))
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help="Benchmark profile name (default: production)")
    parser.add_argument("--target", default=DEFAULT_TARGET, help="Target key from config.json (default: 60s)")
    parser.add_argument("--target-url", default=None, help="Override target video URL")
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--skip-ffprobe", action="store_true")
    args = parser.parse_args()

    try:
        ctx = load_run_context(
            config_path=Path(args.config),
            profile_name=args.profile,
            target_key=args.target,
            target_url_override=args.target_url,
        )
    except (RuntimeError, KeyError) as exc:
        print(exc, file=sys.stderr)
        return 1

    extra_args = ctx.body.get("input", {}).get("extra_args", [])
    print(f"profile={ctx.profile_name} target={ctx.target_key} target_url={ctx.target_url}")
    print(f"extra_args: {' '.join(extra_args)}")
    if "h264_nvenc" not in extra_args and "hevc_nvenc" not in extra_args:
        print(f"[warn] profile '{ctx.profile_name}' does not use an NVENC encoder; fallback cannot trigger")

    run_result = submit(ctx.endpoint_id, ctx.api_key, ctx.body)
    job_id = run_result.get("id")
    print(f"submitted job_id={job_id}")
    status = poll_status(
        ctx.endpoint_id, ctx.api_key, job_id,
        timeout_seconds=args.timeout, poll_interval=ctx.poll_interval,
    )

    if status.get("status") != "COMPLETED":
        print(f"FAIL: job status={status.get('status')}")
        print(json.dumps(status.get("output") or {}, indent=2)[:2000])
        return 2

    output = status.get("output") or {}
    encoder = output.get("encoder")
    output_url = output.get("output_url")
    print(f"output.encoder = {json.dumps(encoder)}")
    print(f"output_url = {output_url}")

    if not output_url:
        print("FAIL: no output_url in response")
        return 3
    if not isinstance(encoder, dict):
        print("FAIL: response missing output.encoder — is the deployed image up to date?")
        return 3

    expected_codec = "h264"  # both h264_nvenc and libx264 produce h264 streams
    if encoder["nvenc_available"] and encoder["fallback_applied"]:
        print("FAIL: fallback applied even though NVENC was reported available")
        return 3
    if not encoder["nvenc_available"] and not encoder["fallback_applied"] and "h264_nvenc" in extra_args:
        print("FAIL: NVENC unavailable but fallback was not applied")
        return 3

    if not args.skip_ffprobe:
        codec = _probe_output_codec(output_url)
        if codec:
            print(f"ffprobe codec = {codec}")
            if codec != expected_codec:
                print(f"FAIL: expected {expected_codec} stream, got {codec}")
                return 3

    mode = "NVENC (GPU)" if encoder["nvenc_available"] else "libx264 fallback (CPU)"
    print(f"PASS: job completed using {mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
