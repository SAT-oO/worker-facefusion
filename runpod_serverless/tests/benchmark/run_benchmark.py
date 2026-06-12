#!/usr/bin/env python3
"""Legacy generic benchmark harness. Prefer test_1/run.py and test_2/run.py for formal tests."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from benchmark_common import (
    BENCHMARK_DIR,
    build_record,
    load_config,
    load_run_context,
    poll_job,
    print_failure_summary,
    print_record_progress,
    print_run_debug,
    run_sequential,
    submit_job,
    utc_now,
    write_run_artifacts,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="RunPod benchmark harness (legacy)")
    parser.add_argument("--config", default=str(BENCHMARK_DIR / "config.json"))
    parser.add_argument("--scenario", required=True, choices=["cold_flashboot", "warm", "concurrent"])
    parser.add_argument("--profile", required=True)
    parser.add_argument("--target", default=None)
    parser.add_argument("--target-url", default=None)
    parser.add_argument("--iterations", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--wait-after-job", type=int, default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    config_path = Path(args.config)
    config = load_config(config_path)
    target_key = args.target or config["fixtures"].get("default_target_key", "120s")

    try:
        ctx = load_run_context(
            config_path=config_path,
            profile_name=args.profile,
            target_key=target_key,
            target_url_override=args.target_url,
        )
    except (RuntimeError, KeyError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1

    print_run_debug(ctx)

    scenario_cfg = config["scenarios"][args.scenario]
    iterations = args.iterations if args.iterations is not None else scenario_cfg.get("iterations", 1)
    concurrency = args.concurrency if args.concurrency is not None else scenario_cfg.get("concurrency", 1)
    wait_after_job = (
        args.wait_after_job if args.wait_after_job is not None else scenario_cfg.get("wait_after_job_seconds", 0)
    )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = (
        Path(args.output)
        if args.output
        else BENCHMARK_DIR / "results" / f"{timestamp}_{args.scenario}_{args.profile}.jsonl"
    )

    if args.scenario == "warm" and concurrency == 1 and wait_after_job == 0:
        print("[note] For Test 1, prefer: python3 runpod_serverless/tests/benchmark/test_1/run.py", file=sys.stderr)
        return run_sequential(
            ctx=ctx,
            test_id="legacy",
            scenario=args.scenario,
            iterations=iterations,
            output_path=output_path,
        )

    records: list[dict] = []
    print(f"scenario={args.scenario} profile={args.profile} target={ctx.target_key}")
    print(f"iterations={iterations} concurrency={concurrency} wait_after_job={wait_after_job}s")
    print(f"endpoint={ctx.endpoint_id}")
    print(f"writing results to {output_path}")

    for batch_start in range(0, iterations, concurrency):
        batch_size = min(concurrency, iterations - batch_start)
        batch_indices = list(range(batch_start + 1, batch_start + batch_size + 1))

        if batch_start > 0 and wait_after_job > 0:
            print(f"[wait] idle {wait_after_job}s for scale-to-zero / FlashBoot snapshot")
            time.sleep(wait_after_job)

        if concurrency == 1:
            record = _run_single_legacy(ctx, args.scenario, batch_indices[0])
            records.append(record)
            print_record_progress(record, iterations)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=batch_size) as pool:
                futures = [pool.submit(_run_single_legacy, ctx, args.scenario, i) for i in batch_indices]
                for future in concurrent.futures.as_completed(futures):
                    record = future.result()
                    records.append(record)
                    print_record_progress(record, iterations)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("a", encoding="utf-8") as fh:
            for record in records[-batch_size:]:
                fh.write(json.dumps(record) + "\n")

    run_meta = {
        "test_id": "legacy",
        "scenario": args.scenario,
        "profile": ctx.profile_name,
        "target_key": ctx.target_key,
        "iterations": iterations,
        "concurrency": concurrency,
        "endpoint_id": ctx.endpoint_id,
    }
    summary = write_run_artifacts(records=records, output_path=output_path, run_meta=run_meta)
    print_failure_summary(records)
    print("\nSummary:")
    print(json.dumps(summary, indent=2))
    return 0 if summary["failed"] == 0 else 2


def _run_single_legacy(ctx, scenario: str, iteration: int) -> dict:
    submitted_at = utc_now()
    submit_mono = time.monotonic()
    job_id = submit_job(ctx.endpoint_id, ctx.api_key, ctx.body)
    status = poll_job(ctx.endpoint_id, ctx.api_key, job_id, ctx.job_timeout_seconds, ctx.poll_interval)
    return build_record(
        status=status,
        test_id="legacy",
        scenario=scenario,
        profile_name=ctx.profile_name,
        target_key=ctx.target_key,
        iteration=iteration,
        job_id=job_id,
        submitted_at=submitted_at,
        submit_mono=submit_mono,
        cold_threshold_ms=ctx.cold_threshold_ms,
    )


if __name__ == "__main__":
    raise SystemExit(main())
