#!/usr/bin/env python3
"""RunPod serverless benchmark harness (FlashBoot-aware cold-start testing)."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


DIR = Path(__file__).resolve().parent
RUNPOD_API = "https://api.runpod.ai/v2"
DEBUG_LOG_PATH = Path("/Users/sat-oo/worker-facefusion/.cursor/debug-31ce53.log")


def _agent_log(hypothesis_id: str, location: str, message: str, data: dict[str, Any], run_id: str = "pre-fix") -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": "31ce53",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
    except OSError:
        pass
    # #endregion


def _extract_failure_details(status: dict[str, Any]) -> dict[str, Any]:
    output = status.get("output") if isinstance(status.get("output"), dict) else {}
    handler_error = output.get("error") if isinstance(output, dict) else None
    runpod_error = status.get("error")
    return {
        "handler_error": handler_error,
        "runpod_error": runpod_error,
        "failure_message": handler_error or runpod_error,
        "stderr_tail": (output.get("stderr") or "")[-500:] if isinstance(output, dict) else "",
        "stdout_tail": (output.get("stdout") or "")[-500:] if isinstance(output, dict) else "",
        "output_keys": sorted(output.keys()) if isinstance(output, dict) else [],
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        config = json.load(fh)
    return _expand_env(config)


def _api_request(method: str, url: str, api_key: str, payload: dict | None = None) -> dict[str, Any]:
    data = None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"RunPod API {method} {url} failed ({exc.code}): {body}") from exc


def submit_job(endpoint_id: str, api_key: str, body: dict[str, Any]) -> str:
    result = _api_request("POST", f"{RUNPOD_API}/{endpoint_id}/run", api_key, body)
    job_id = result.get("id")
    if not job_id:
        raise RuntimeError(f"RunPod /run returned no job id: {result}")
    return job_id


def poll_job(endpoint_id: str, api_key: str, job_id: str, timeout_seconds: int, poll_interval: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = _api_request("GET", f"{RUNPOD_API}/{endpoint_id}/status/{job_id}", api_key)
        status = result.get("status")
        if status in {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}:
            return result
        time.sleep(poll_interval)
    raise TimeoutError(f"job {job_id} did not finish within {timeout_seconds}s")


def build_request_body(config: dict[str, Any], profile: dict[str, Any], target_url: str, source_b64: str) -> dict[str, Any]:
    base = config.get("base_input", {})
    input_payload = {
        "source_image_base64": source_b64,
        "source_image_format": config["fixtures"].get("source_image_format", "jpg"),
        "target_url": target_url,
        "output_format": base.get("output_format", "mp4"),
        "processors": base.get("processors", ["face_swapper"]),
        "face_swapper_model": base.get("face_swapper_model"),
        "extra_args": profile.get("extra_args", []),
    }
    body: dict[str, Any] = {"input": input_payload}
    if base.get("policy"):
        body["policy"] = base["policy"]
    return body


def classify_cold_start(delay_ms: int | None, threshold_ms: int) -> str:
    if delay_ms is None:
        return "unknown"
    if delay_ms <= threshold_ms:
        return "flashboot_restore_or_warm"
    return "fresh_boot_or_miss"


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((pct / 100) * (len(ordered) - 1)))))
    return ordered[index]


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if r.get("status") == "COMPLETED" and not r.get("error")]
    failed = [r for r in records if r not in ok]

    def nums(key: str) -> list[float]:
        return [float(r[key]) for r in ok if isinstance(r.get(key), (int, float))]

    delay = nums("delay_time_ms")
    execution = nums("execution_time_ms")
    total = nums("total_time_ms")

    cold_counts: dict[str, int] = {}
    for record in ok:
        label = record.get("cold_start_class", "unknown")
        cold_counts[label] = cold_counts.get(label, 0) + 1

    return {
        "samples": len(records),
        "success": len(ok),
        "failed": len(failed),
        "delay_time_ms": {
            "p50": percentile(delay, 50),
            "p90": percentile(delay, 90),
            "p99": percentile(delay, 99),
            "mean": mean(delay) if delay else None,
        },
        "execution_time_ms": {
            "p50": percentile(execution, 50),
            "p90": percentile(execution, 90),
            "p99": percentile(execution, 99),
            "mean": mean(execution) if execution else None,
        },
        "total_time_ms": {
            "p50": percentile(total, 50),
            "p90": percentile(total, 90),
            "p99": percentile(total, 99),
            "mean": mean(total) if total else None,
        },
        "cold_start_class_counts": cold_counts,
    }


def run_single_job(
    *,
    endpoint_id: str,
    api_key: str,
    body: dict[str, Any],
    scenario: str,
    profile_name: str,
    target_key: str,
    iteration: int,
    job_timeout_seconds: int,
    poll_interval: float,
    cold_threshold_ms: int,
) -> dict[str, Any]:
    submitted_at = _utc_now()
    submit_mono = time.monotonic()
    job_id = submit_job(endpoint_id, api_key, body)
    status = poll_job(endpoint_id, api_key, job_id, job_timeout_seconds, poll_interval)
    finished_mono = time.monotonic()

    output = status.get("output") if isinstance(status.get("output"), dict) else {}
    delay_time_ms = status.get("delayTime")
    execution_time_ms = status.get("executionTime")
    handler_timings = output.get("timings") if isinstance(output, dict) else None
    failure = _extract_failure_details(status)
    error = failure.get("failure_message")

    total_time_ms = int((finished_mono - submit_mono) * 1000)

    record = {
        "scenario": scenario,
        "profile": profile_name,
        "target_key": target_key,
        "iteration": iteration,
        "job_id": job_id,
        "submitted_at": submitted_at,
        "status": status.get("status"),
        "delay_time_ms": delay_time_ms,
        "execution_time_ms": execution_time_ms,
        "total_time_ms": total_time_ms,
        "cold_start_class": classify_cold_start(delay_time_ms, cold_threshold_ms),
        "handler_timings": handler_timings,
        "error": error,
        "handler_error": failure.get("handler_error"),
        "runpod_error": failure.get("runpod_error"),
        "stderr_tail": failure.get("stderr_tail"),
        "stdout_tail": failure.get("stdout_tail"),
        "output_url": output.get("output_url") if isinstance(output, dict) else None,
    }

    # #region agent log
    _agent_log(
        "H1",
        "run_benchmark.py:run_single_job",
        "job finished",
        {
            "job_id": job_id,
            "status": record["status"],
            "execution_time_ms": execution_time_ms,
            "facefusion_ms": (handler_timings or {}).get("facefusion_ms"),
            "upload_output_ms": (handler_timings or {}).get("upload_output_ms"),
            "failure": failure,
        },
    )
    # #endregion

    return record


def _sample_input_target_url() -> str | None:
    sample_path = DIR.parent / "sample_input.json"
    if not sample_path.exists():
        return None
    with sample_path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    url = (data.get("input") or {}).get("target_url")
    return url if url and not url.startswith("${") else None


def resolve_target_url(
    config: dict[str, Any],
    target_key: str,
    *,
    override_url: str | None = None,
) -> str:
    if override_url:
        return override_url

    targets = config.get("targets", {})
    url = targets.get(target_key)
    if not url:
        raise KeyError(f"target key '{target_key}' not found in config targets")
    if not (url.startswith("${") and url.endswith("}")):
        return url

    default_url = config.get("fixtures", {}).get("default_target_url")
    if default_url and not (default_url.startswith("${") and default_url.endswith("}")):
        print(
            f"[warn] target '{target_key}' unset ({url}); using fixtures.default_target_url",
            file=sys.stderr,
        )
        return default_url

    sample_url = _sample_input_target_url()
    if sample_url:
        print(
            f"[warn] target '{target_key}' unset ({url}); using runpod_serverless/tests/sample_input.json target_url",
            file=sys.stderr,
        )
        return sample_url

    raise ValueError(
        f"target '{target_key}' is unset ({url}). Set the env var, add a URL in config.json targets, "
        f"pass --target-url, or set target_url in runpod_serverless/tests/sample_input.json"
    )


def resolve_profile(config: dict[str, Any], profile_name: str) -> dict[str, Any]:
    for profile in config.get("profiles", []):
        if profile.get("name") == profile_name:
            return profile
    raise KeyError(f"profile '{profile_name}' not found")


def main() -> int:
    parser = argparse.ArgumentParser(description="RunPod benchmark harness")
    parser.add_argument("--config", default=str(DIR / "config.json"), help="Path to benchmark config JSON")
    parser.add_argument("--scenario", required=True, choices=["cold_flashboot", "warm", "concurrent"])
    parser.add_argument("--profile", required=True, help="Profile name from config.profiles")
    parser.add_argument("--target", default=None, help="Target key from config.targets (default: fixtures.default_target_key)")
    parser.add_argument("--target-url", default=None, help="Override target video URL (skips config.targets)")
    parser.add_argument("--iterations", type=int, default=None, help="Override scenario iterations")
    parser.add_argument("--concurrency", type=int, default=None, help="Override scenario concurrency")
    parser.add_argument("--wait-after-job", type=int, default=None, help="Override idle wait seconds between jobs")
    parser.add_argument("--output", default=None, help="Output JSONL path (default: results/<timestamp>.jsonl)")
    args = parser.parse_args()

    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        print("RUNPOD_API_KEY is required", file=sys.stderr)
        return 1

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        print("Copy config.example.json to config.json and fill in target URLs.", file=sys.stderr)
        return 1

    config = load_config(config_path)
    endpoint_id = config.get("endpoint_id")
    if not endpoint_id or endpoint_id.startswith("${"):
        print("Set endpoint_id in config.json or ENDPOINT_ID env var", file=sys.stderr)
        return 1

    scenario_cfg = config["scenarios"][args.scenario]
    profile = resolve_profile(config, args.profile)
    target_key = args.target or config["fixtures"].get("default_target_key", "120s")
    target_url = resolve_target_url(config, target_key, override_url=args.target_url)

    source_b64_path = (config_path.parent / config["fixtures"]["source_b64_path"]).resolve()
    if not source_b64_path.exists():
        print(f"Missing source b64 fixture: {source_b64_path}", file=sys.stderr)
        print("Run: bash runpod_serverless/tests/fetch_fixtures.sh", file=sys.stderr)
        return 1
    source_b64 = source_b64_path.read_text(encoding="utf-8").strip()

    iterations = args.iterations if args.iterations is not None else scenario_cfg.get("iterations", 1)
    concurrency = args.concurrency if args.concurrency is not None else scenario_cfg.get("concurrency", 1)
    wait_after_job = (
        args.wait_after_job
        if args.wait_after_job is not None
        else scenario_cfg.get("wait_after_job_seconds", 0)
    )

    runpod_cfg = config.get("runpod", {})
    poll_interval = float(runpod_cfg.get("poll_interval_seconds", 2))
    job_timeout_seconds = int(runpod_cfg.get("job_timeout_seconds", 1800))
    cold_threshold_ms = int(runpod_cfg.get("flashboot_cold_threshold_ms", 30000))

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = Path(args.output) if args.output else DIR / "results" / f"{timestamp}_{args.scenario}_{args.profile}.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    body = build_request_body(config, profile, target_url, source_b64)
    records: list[dict[str, Any]] = []

    print(f"scenario={args.scenario} profile={args.profile} target={target_key}")
    print(f"iterations={iterations} concurrency={concurrency} wait_after_job={wait_after_job}s")
    print(f"endpoint={endpoint_id}")
    print(f"writing results to {output_path}")

    for batch_start in range(0, iterations, concurrency):
        batch_size = min(concurrency, iterations - batch_start)
        batch_indices = list(range(batch_start + 1, batch_start + batch_size + 1))

        if batch_start > 0 and wait_after_job > 0:
            print(f"[wait] idle {wait_after_job}s for scale-to-zero / FlashBoot snapshot")
            time.sleep(wait_after_job)

        if concurrency == 1:
            record = run_single_job(
                endpoint_id=endpoint_id,
                api_key=api_key,
                body=body,
                scenario=args.scenario,
                profile_name=profile["name"],
                target_key=target_key,
                iteration=batch_indices[0],
                job_timeout_seconds=job_timeout_seconds,
                poll_interval=poll_interval,
                cold_threshold_ms=cold_threshold_ms,
            )
            records.append(record)
            print(
                f"[{record['iteration']}/{iterations}] status={record['status']} "
                f"delay={record['delay_time_ms']}ms exec={record['execution_time_ms']}ms "
                f"class={record['cold_start_class']}"
            )
            if record.get("error"):
                print(f"  error: {record['error']}")
            if record.get("stderr_tail"):
                print(f"  stderr: {record['stderr_tail'][:300]}")
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=batch_size) as pool:
                futures = [
                    pool.submit(
                        run_single_job,
                        endpoint_id=endpoint_id,
                        api_key=api_key,
                        body=body,
                        scenario=args.scenario,
                        profile_name=profile["name"],
                        target_key=target_key,
                        iteration=iteration,
                        job_timeout_seconds=job_timeout_seconds,
                        poll_interval=poll_interval,
                        cold_threshold_ms=cold_threshold_ms,
                    )
                    for iteration in batch_indices
                ]
                for future in concurrent.futures.as_completed(futures):
                    record = future.result()
                    records.append(record)
                    print(
                        f"[{record['iteration']}/{iterations}] status={record['status']} "
                        f"delay={record['delay_time_ms']}ms exec={record['execution_time_ms']}ms"
                    )

        with output_path.open("a", encoding="utf-8") as fh:
            for record in records[-batch_size:]:
                fh.write(json.dumps(record) + "\n")

    summary = summarize(records)
    summary_path = output_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print("\nSummary:")
    print(json.dumps(summary, indent=2))
    print(f"Wrote {output_path}")
    print(f"Wrote {summary_path}")

    if records:
        last = max(records, key=lambda r: r.get("iteration", 0))
        print(f"\nlast_job_id={last.get('job_id', '')}")
        if last.get("output_url"):
            print(f"output_url={last['output_url']}")

    return 0 if summary["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
