"""Shared helpers for RunPod benchmark test scripts."""

from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

BENCHMARK_DIR = Path(__file__).resolve().parent
RUNPOD_API = "https://api.runpod.ai/v2"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return expand_env(json.load(fh))


def api_request(method: str, url: str, api_key: str, payload: dict | None = None) -> dict[str, Any]:
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


def submit_job(endpoint_id: str, api_key: str, body: dict[str, Any]) -> str:
    result = api_request("POST", f"{RUNPOD_API}/{endpoint_id}/run", api_key, body)
    job_id = result.get("id")
    if not job_id:
        raise RuntimeError(f"RunPod /run returned no job id: {result}")
    return job_id


def poll_job(endpoint_id: str, api_key: str, job_id: str, timeout_seconds: int, poll_interval: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = api_request("GET", f"{RUNPOD_API}/{endpoint_id}/status/{job_id}", api_key)
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


def resolve_profile(config: dict[str, Any], profile_name: str) -> dict[str, Any]:
    for profile in config.get("profiles", []):
        if profile.get("name") == profile_name:
            return profile
    raise KeyError(f"profile '{profile_name}' not found")


def _is_unset_url(url: str | None) -> bool:
    return not url or (url.startswith("${") and url.endswith("}"))


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
    if not _is_unset_url(url):
        return url

    raise ValueError(
        f"target '{target_key}' is unset ({url}). "
        f"Set BENCHMARK_TARGET_{target_key.upper()}_URL, --target-url, or config targets"
    )


NVENC_ENCODERS = frozenset({"h264_nvenc", "hevc_nvenc"})


def profile_requests_nvenc(body: dict[str, Any]) -> bool:
    extra_args = body.get("input", {}).get("extra_args", []) or []
    return any(str(arg) in NVENC_ENCODERS for arg in extra_args)


def encoder_check_error(record: dict[str, Any], expects_nvenc: bool) -> str | None:
    """Validate the handler-reported encoder state against the requested profile.

    Returns an error string when the response is inconsistent, else None.
    Records from workers running an old image (no encoder field) are skipped.
    """
    if record.get("status") != "COMPLETED" or record.get("error"):
        return None
    encoder = record.get("encoder")
    if not isinstance(encoder, dict):
        return None  # old image; nothing to check
    nvenc_available = encoder.get("nvenc_available")
    fallback_applied = encoder.get("fallback_applied")
    if nvenc_available and fallback_applied:
        return "fallback applied even though NVENC was reported available"
    if expects_nvenc and not nvenc_available and not fallback_applied:
        return "NVENC unavailable but fallback was not applied to an nvenc job"
    if not expects_nvenc and fallback_applied:
        return "fallback applied to a job that did not request an NVENC encoder"
    return None


def apply_encoder_checks(records: list[dict[str, Any]], expects_nvenc: bool) -> int:
    """Annotate records with encoder consistency failures; returns failure count."""
    failures = 0
    for record in records:
        error = encoder_check_error(record, expects_nvenc)
        if error:
            record["encoder_check_error"] = error
            failures += 1
            print(
                f"  [encoder-check FAILED] iteration={record.get('iteration')} "
                f"job_id={record.get('job_id')}: {error}",
                file=sys.stderr,
            )
    return failures


BURST_SCENARIOS = frozenset({"burst_scaling", "burst", "concurrent"})
TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"})
OOM_MARKERS = ("cuda out of memory", "out of memory", "cudamalloc", "onnxruntime")


def classify_delay(delay_ms: int | None, threshold_ms: int, scenario: str) -> str:
    """Classify RunPod delayTime: warm, queue wait (burst/soak), or cold boot (sequential)."""
    if delay_ms is None:
        return "unknown"
    if delay_ms <= threshold_ms:
        return "warm"
    if scenario in BURST_SCENARIOS or "burst" in scenario or "soak" in scenario:
        return "queued"
    return "cold_boot"


def get_job_status(endpoint_id: str, api_key: str, job_id: str) -> dict[str, Any]:
    return api_request("GET", f"{RUNPOD_API}/{endpoint_id}/status/{job_id}", api_key)


def is_oom_error(record: dict[str, Any]) -> bool:
    text = " ".join(
        str(record.get(key) or "")
        for key in ("error", "handler_error", "stderr_tail")
    ).lower()
    return any(marker in text for marker in OOM_MARKERS)


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((pct / 100) * (len(ordered) - 1)))))
    return ordered[index]


def percentile_stats(values: list[float]) -> dict[str, float | None]:
    return {
        "p50": percentile(values, 50),
        "p75": percentile(values, 75),
        "p90": percentile(values, 90),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "mean": mean(values) if values else None,
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if r.get("status") == "COMPLETED" and not r.get("error")]
    failed = [r for r in records if r not in ok]

    def nums(key: str) -> list[float]:
        return [float(r[key]) for r in ok if isinstance(r.get(key), (int, float))]

    delay = nums("delay_time_ms")
    execution = nums("execution_time_ms")
    total = nums("total_time_ms")

    handler_phases: dict[str, list[float]] = {}
    for record in ok:
        timings = record.get("handler_timings") or {}
        for phase, value in timings.items():
            if isinstance(value, (int, float)):
                handler_phases.setdefault(phase, []).append(float(value))

    delay_class_counts: dict[str, int] = {}
    for record in ok:
        label = record.get("delay_class") or record.get("cold_start_class", "unknown")
        delay_class_counts[label] = delay_class_counts.get(label, 0) + 1

    encoder_counts: dict[str, int] = {}
    for record in ok:
        encoder = record.get("encoder")
        if not isinstance(encoder, dict):
            label = "unknown"
        elif encoder.get("fallback_applied"):
            label = "cpu_fallback"
        elif encoder.get("nvenc_available"):
            label = "nvenc"
        else:
            label = "cpu"
        encoder_counts[label] = encoder_counts.get(label, 0) + 1

    return {
        "samples": len(records),
        "success": len(ok),
        "failed": len(failed),
        "delay_time_ms": percentile_stats(delay),
        "execution_time_ms": percentile_stats(execution),
        "total_time_ms": percentile_stats(total),
        "handler_timings_ms": {
            phase: percentile_stats(values) for phase, values in sorted(handler_phases.items())
        },
        "delay_class_counts": delay_class_counts,
        "encoder_counts": encoder_counts,
        "encoder_check_failures": [
            {
                "iteration": record.get("iteration"),
                "job_id": record.get("job_id"),
                "error": record.get("encoder_check_error"),
                "encoder": record.get("encoder"),
            }
            for record in records
            if record.get("encoder_check_error")
        ],
        "failures": [
            {
                "iteration": record.get("iteration"),
                "job_id": record.get("job_id"),
                "error": record.get("error"),
                "stderr_tail": (record.get("stderr_tail") or "").strip() or None,
                "stdout_tail": (record.get("stdout_tail") or "").strip() or None,
            }
            for record in failed
        ],
    }


def extract_failure_details(status: dict[str, Any]) -> dict[str, Any]:
    output = status.get("output") if isinstance(status.get("output"), dict) else {}
    handler_error = output.get("error") if isinstance(output, dict) else None
    runpod_error = status.get("error")
    return {
        "handler_error": handler_error,
        "runpod_error": runpod_error,
        "failure_message": handler_error or runpod_error,
        "stderr_tail": (output.get("stderr") or "")[-500:] if isinstance(output, dict) else "",
        "stdout_tail": (output.get("stdout") or "")[-500:] if isinstance(output, dict) else "",
    }


def build_record(
    *,
    status: dict[str, Any],
    test_id: str,
    scenario: str,
    profile_name: str,
    target_key: str,
    iteration: int,
    job_id: str,
    submitted_at: str,
    submit_mono: float,
    cold_threshold_ms: int,
    endpoint_notes: str | None = None,
) -> dict[str, Any]:
    finished_mono = time.monotonic()
    output = status.get("output") if isinstance(status.get("output"), dict) else {}
    delay_time_ms = status.get("delayTime")
    execution_time_ms = status.get("executionTime")
    handler_timings = output.get("timings") if isinstance(output, dict) else None
    failure = extract_failure_details(status)

    record: dict[str, Any] = {
        "test_id": test_id,
        "scenario": scenario,
        "profile": profile_name,
        "target_key": target_key,
        "iteration": iteration,
        "job_id": job_id,
        "submitted_at": submitted_at,
        "status": status.get("status"),
        "delay_time_ms": delay_time_ms,
        "execution_time_ms": execution_time_ms,
        "total_time_ms": int((finished_mono - submit_mono) * 1000),
        "delay_class": classify_delay(delay_time_ms, cold_threshold_ms, scenario),
        "handler_timings": handler_timings,
        "error": failure.get("failure_message"),
        "handler_error": failure.get("handler_error"),
        "runpod_error": failure.get("runpod_error"),
        "stderr_tail": failure.get("stderr_tail"),
        "stdout_tail": failure.get("stdout_tail"),
        "output_url": output.get("output_url") if isinstance(output, dict) else None,
        "encoder": output.get("encoder") if isinstance(output, dict) else None,
    }
    if endpoint_notes:
        record["endpoint_notes"] = endpoint_notes
    return record


@dataclass
class RunContext:
    config_path: Path
    config: dict[str, Any]
    endpoint_id: str
    api_key: str
    profile: dict[str, Any]
    profile_name: str
    target_key: str
    target_url: str
    body: dict[str, Any]
    poll_interval: float
    job_timeout_seconds: int
    cold_threshold_ms: int


def load_run_context(
    *,
    config_path: Path,
    profile_name: str,
    target_key: str,
    target_url_override: str | None = None,
) -> RunContext:
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        raise RuntimeError("RUNPOD_API_KEY is required")

    if not config_path.exists():
        raise RuntimeError(f"Config not found: {config_path}")

    config = load_config(config_path)
    endpoint_id = config.get("endpoint_id")
    if not endpoint_id or str(endpoint_id).startswith("${"):
        raise RuntimeError("Set endpoint_id in config.json or ENDPOINT_ID env var")

    profile = resolve_profile(config, profile_name)
    target_url = resolve_target_url(config, target_key, override_url=target_url_override)

    source_b64_path = (config_path.parent / config["fixtures"]["source_b64_path"]).resolve()
    if not source_b64_path.exists():
        raise RuntimeError(f"Missing source b64 fixture: {source_b64_path} (run fetch_fixtures.sh)")

    source_b64 = source_b64_path.read_text(encoding="utf-8").strip()
    body = build_request_body(config, profile, target_url, source_b64)

    runpod_cfg = config.get("runpod", {})
    return RunContext(
        config_path=config_path,
        config=config,
        endpoint_id=endpoint_id,
        api_key=api_key,
        profile=profile,
        profile_name=profile_name,
        target_key=target_key,
        target_url=target_url,
        body=body,
        poll_interval=float(runpod_cfg.get("poll_interval_seconds", 2)),
        job_timeout_seconds=int(runpod_cfg.get("job_timeout_seconds", 1800)),
        cold_threshold_ms=int(runpod_cfg.get("flashboot_cold_threshold_ms", 30000)),
    )


def default_output_path(test_id: str, label: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_label = label.replace(" ", "_")
    return BENCHMARK_DIR / "results" / test_id / f"{timestamp}_{safe_label}.jsonl"


def load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def write_run_artifacts(
    *,
    records: list[dict[str, Any]],
    output_path: Path,
    run_meta: dict[str, Any],
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")

    summary = summarize(records)
    summary = {"run_meta": run_meta, **summary}
    summary_path = output_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def print_run_debug(ctx: RunContext) -> None:
    profile = ctx.profile
    extra_args = profile.get("extra_args", [])
    print(f"profile={ctx.profile_name}")
    if profile.get("description"):
        print(f"profile_description={profile['description']}")
    print(f"target={ctx.target_key}")
    print(f"target_url={ctx.target_url}")
    print(f"extra_args: {' '.join(extra_args) if extra_args else '(none)'}")


def print_failure_summary(records: list[dict[str, Any]]) -> None:
    failed = [
        record
        for record in records
        if record.get("status") != "COMPLETED" or record.get("error")
    ]
    if not failed:
        return

    print("\nFailure details:")
    for record in failed:
        print(f"  iteration={record.get('iteration')} job_id={record.get('job_id')}")
        print(f"  error: {record.get('error')}")
        stderr_tail = (record.get("stderr_tail") or "").strip()
        if stderr_tail:
            print("  stderr_tail:")
            for line in stderr_tail.splitlines()[-10:]:
                print(f"    {line}")
        else:
            print("  stderr_tail: (empty — check JSONL or RunPod worker logs for job_id)")


def print_record_progress(record: dict[str, Any], iterations: int) -> None:
    encoder = record.get("encoder")
    if isinstance(encoder, dict):
        encoder_label = "cpu_fallback" if encoder.get("fallback_applied") else (
            "nvenc" if encoder.get("nvenc_available") else "cpu"
        )
    else:
        encoder_label = "unknown"
    print(
        f"[{record['iteration']}/{iterations}] status={record['status']} "
        f"delay={record['delay_time_ms']}ms exec={record['execution_time_ms']}ms "
        f"delay_class={record['delay_class']} encoder={encoder_label}"
    )
    if encoder_label == "cpu_fallback":
        print("  [warn] NVENC unavailable on this worker; job encoded on CPU (libx264 fallback)")
    if record.get("error"):
        print(f"  error: {record['error']}")
        stderr_tail = (record.get("stderr_tail") or "").strip()
        if stderr_tail:
            print("  stderr_tail:")
            for line in stderr_tail.splitlines()[-8:]:
                print(f"    {line}")


def run_sequential(
    *,
    ctx: RunContext,
    test_id: str,
    scenario: str,
    iterations: int,
    endpoint_notes: str | None = None,
    output_path: Path,
) -> int:
    records: list[dict[str, Any]] = []

    print(f"test={test_id} scenario={scenario} profile={ctx.profile_name} target={ctx.target_key}")
    print(f"iterations={iterations} mode=sequential")
    if endpoint_notes:
        print(f"endpoint_notes={endpoint_notes}")
    print(f"endpoint={ctx.endpoint_id}")
    print(f"writing results to {output_path}")

    for iteration in range(1, iterations + 1):
        submitted_at = utc_now()
        submit_mono = time.monotonic()
        job_id = submit_job(ctx.endpoint_id, ctx.api_key, ctx.body)
        status = poll_job(
            ctx.endpoint_id,
            ctx.api_key,
            job_id,
            ctx.job_timeout_seconds,
            ctx.poll_interval,
        )
        record = build_record(
            status=status,
            test_id=test_id,
            scenario=scenario,
            profile_name=ctx.profile_name,
            target_key=ctx.target_key,
            iteration=iteration,
            job_id=job_id,
            submitted_at=submitted_at,
            submit_mono=submit_mono,
            cold_threshold_ms=ctx.cold_threshold_ms,
            endpoint_notes=endpoint_notes,
        )
        records.append(record)
        print_record_progress(record, iterations)

    encoder_failures = apply_encoder_checks(records, profile_requests_nvenc(ctx.body))

    run_meta = {
        "test_id": test_id,
        "scenario": scenario,
        "profile": ctx.profile_name,
        "target_key": ctx.target_key,
        "iterations": iterations,
        "mode": "sequential",
        "endpoint_notes": endpoint_notes,
        "endpoint_id": ctx.endpoint_id,
    }
    summary = write_run_artifacts(records=records, output_path=output_path, run_meta=run_meta)
    print_failure_summary(records)
    print("\nSummary:")
    print(json.dumps(summary, indent=2))
    print(f"Wrote {output_path}")
    print(f"Wrote {output_path.with_suffix('.summary.json')}")
    return 0 if summary["failed"] == 0 and encoder_failures == 0 else 2


def run_burst_submit_then_poll(
    *,
    ctx: RunContext,
    test_id: str,
    scenario: str,
    iterations: int,
    endpoint_notes: str | None,
    output_path: Path,
) -> int:
    """Submit all jobs first, then poll in parallel (measures RunPod queue delay under burst)."""
    pending: list[tuple[int, str, str, float]] = []

    print(f"test={test_id} scenario={scenario} profile={ctx.profile_name} target={ctx.target_key}")
    print(f"iterations={iterations} mode=burst_submit_then_poll")
    if endpoint_notes:
        print(f"endpoint_notes={endpoint_notes}")
    print(f"endpoint={ctx.endpoint_id}")
    print(f"writing results to {output_path}")

    print(f"\n[phase 1] submitting {iterations} jobs...")
    for iteration in range(1, iterations + 1):
        submitted_at = utc_now()
        submit_mono = time.monotonic()
        job_id = submit_job(ctx.endpoint_id, ctx.api_key, ctx.body)
        pending.append((iteration, job_id, submitted_at, submit_mono))
        print(f"  submitted [{iteration}/{iterations}] job_id={job_id}")

    print(f"\n[phase 2] polling {iterations} jobs...")

    def poll_one(item: tuple[int, str, str, float]) -> dict[str, Any]:
        iteration, job_id, submitted_at, submit_mono = item
        status = poll_job(
            ctx.endpoint_id,
            ctx.api_key,
            job_id,
            ctx.job_timeout_seconds,
            ctx.poll_interval,
        )
        return build_record(
            status=status,
            test_id=test_id,
            scenario=scenario,
            profile_name=ctx.profile_name,
            target_key=ctx.target_key,
            iteration=iteration,
            job_id=job_id,
            submitted_at=submitted_at,
            submit_mono=submit_mono,
            cold_threshold_ms=ctx.cold_threshold_ms,
            endpoint_notes=endpoint_notes,
        )

    records: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=iterations) as pool:
        futures = [pool.submit(poll_one, item) for item in pending]
        for future in concurrent.futures.as_completed(futures):
            record = future.result()
            records.append(record)
            print_record_progress(record, iterations)

    records.sort(key=lambda r: r.get("iteration", 0))

    encoder_failures = apply_encoder_checks(records, profile_requests_nvenc(ctx.body))

    run_meta = {
        "test_id": test_id,
        "scenario": scenario,
        "profile": ctx.profile_name,
        "target_key": ctx.target_key,
        "iterations": iterations,
        "mode": "burst_submit_then_poll",
        "endpoint_notes": endpoint_notes,
        "endpoint_id": ctx.endpoint_id,
    }
    summary = write_run_artifacts(records=records, output_path=output_path, run_meta=run_meta)
    print_failure_summary(records)
    print("\nSummary:")
    print(json.dumps(summary, indent=2))
    print(f"Wrote {output_path}")
    print(f"Wrote {output_path.with_suffix('.summary.json')}")
    return 0 if summary["failed"] == 0 and encoder_failures == 0 else 2


def rolling_window_summary(records: list[dict[str, Any]], window_size: int) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for start in range(0, len(records), window_size):
        chunk = records[start : start + window_size]
        if not chunk:
            continue
        stats = summarize(chunk)
        windows.append(
            {
                "window": len(windows) + 1,
                "jobs": f"{start + 1}-{start + len(chunk)}",
                "success": stats["success"],
                "failed": stats["failed"],
                "execution_time_ms_p50": stats["execution_time_ms"]["p50"],
                "execution_time_ms_p99": stats["execution_time_ms"]["p99"],
            }
        )
    return windows


def run_soak(
    *,
    ctx: RunContext,
    test_id: str,
    scenario: str,
    total_jobs: int,
    rate: float,
    max_inflight: int,
    window_size: int,
    endpoint_notes: str | None,
    output_path: Path,
) -> int:
    """Submit jobs at a steady rate; poll in-flight jobs until all complete."""
    if rate <= 0:
        raise ValueError("rate must be > 0")
    if max_inflight < 1:
        raise ValueError("max_inflight must be >= 1")

    records: list[dict[str, Any]] = []
    in_flight: dict[str, dict[str, Any]] = {}
    next_iteration = 1
    last_submit_mono = 0.0
    submit_interval = 1.0 / rate
    oom_count = 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("", encoding="utf-8")

    print(f"test={test_id} scenario={scenario} profile={ctx.profile_name} target={ctx.target_key}")
    print(f"total_jobs={total_jobs} rate={rate}/s max_inflight={max_inflight} window_size={window_size}")
    if endpoint_notes:
        print(f"endpoint_notes={endpoint_notes}")
    print(f"endpoint={ctx.endpoint_id}")
    print(f"writing results to {output_path}")

    while next_iteration <= total_jobs or in_flight:
        now = time.monotonic()

        while (
            next_iteration <= total_jobs
            and len(in_flight) < max_inflight
            and (next_iteration == 1 or now - last_submit_mono >= submit_interval)
        ):
            submitted_at = utc_now()
            submit_mono = time.monotonic()
            job_id = submit_job(ctx.endpoint_id, ctx.api_key, ctx.body)
            in_flight[job_id] = {
                "iteration": next_iteration,
                "submitted_at": submitted_at,
                "submit_mono": submit_mono,
            }
            print(f"[submit {next_iteration}/{total_jobs}] job_id={job_id} in_flight={len(in_flight)}")
            next_iteration += 1
            last_submit_mono = time.monotonic()
            now = last_submit_mono

        finished_ids: list[str] = []
        for job_id, pending in list(in_flight.items()):
            status_payload = get_job_status(ctx.endpoint_id, ctx.api_key, job_id)
            if status_payload.get("status") not in TERMINAL_STATUSES:
                continue

            record = build_record(
                status=status_payload,
                test_id=test_id,
                scenario=scenario,
                profile_name=ctx.profile_name,
                target_key=ctx.target_key,
                iteration=pending["iteration"],
                job_id=job_id,
                submitted_at=pending["submitted_at"],
                submit_mono=pending["submit_mono"],
                cold_threshold_ms=ctx.cold_threshold_ms,
                endpoint_notes=endpoint_notes,
            )
            records.append(record)
            if is_oom_error(record):
                oom_count += 1

            with output_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")

            print_record_progress(record, total_jobs)
            finished_ids.append(job_id)

            if len(records) % window_size == 0:
                window = rolling_window_summary(records, window_size)[-1]
                print(f"[window {window['window']}] jobs {window['jobs']} exec_p99={window['execution_time_ms_p99']}ms")

        for job_id in finished_ids:
            del in_flight[job_id]

        if in_flight or next_iteration <= total_jobs:
            time.sleep(ctx.poll_interval)

    encoder_failures = apply_encoder_checks(records, profile_requests_nvenc(ctx.body))

    windows = rolling_window_summary(records, window_size)
    degradation_ratio = None
    if len(windows) >= 2 and windows[0]["execution_time_ms_p99"]:
        baseline = float(windows[0]["execution_time_ms_p99"])
        latest = float(windows[-1]["execution_time_ms_p99"] or 0)
        if baseline > 0:
            degradation_ratio = round(latest / baseline, 3)

    run_meta = {
        "test_id": test_id,
        "scenario": scenario,
        "profile": ctx.profile_name,
        "target_key": ctx.target_key,
        "total_jobs": total_jobs,
        "rate_per_second": rate,
        "max_inflight": max_inflight,
        "window_size": window_size,
        "mode": "soak_rate_limited",
        "endpoint_notes": endpoint_notes,
        "endpoint_id": ctx.endpoint_id,
        "oom_count": oom_count,
        "rolling_windows": windows,
        "execution_p99_degradation_ratio": degradation_ratio,
    }
    summary = write_run_artifacts(records=records, output_path=output_path, run_meta=run_meta)
    print_failure_summary(records)
    print("\nSummary:")
    print(json.dumps(summary, indent=2))
    print(f"Wrote {output_path}")
    print(f"Wrote {output_path.with_suffix('.summary.json')}")
    if degradation_ratio and degradation_ratio > 1.25:
        print(f"[warn] execution P99 drift: {degradation_ratio}x vs first window", file=sys.stderr)
    return 0 if summary["failed"] == 0 and oom_count == 0 and encoder_failures == 0 else 2
