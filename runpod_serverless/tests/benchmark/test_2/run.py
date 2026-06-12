#!/usr/bin/env python3
"""
Test 2 — Horizontal scaling (burst queue delay).

See test_2/README.md for methodology. Run twice with different RunPod max_workers.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BENCHMARK_DIR = Path(__file__).resolve().parent.parent
if str(_BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCHMARK_DIR))

from benchmark_common import BENCHMARK_DIR, default_output_path, load_run_context, run_burst_submit_then_poll

TEST_ID = "test_2"
DEFAULT_PROFILE = "production"
DEFAULT_TARGET = "60s"
DEFAULT_ITERATIONS = 30
DEFAULT_SCENARIO = "burst_scaling"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test 2: burst N jobs to measure queue delay vs RunPod max_workers",
    )
    parser.add_argument("--config", default=str(BENCHMARK_DIR / "config.json"))
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--target-url", default=None, help="Override target video URL")
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument(
        "--endpoint-notes",
        required=True,
        help="Record RunPod console settings for this run, e.g. 'max_workers=1,min_workers=0'",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="JSONL output path (default: results/test_2/<timestamp>_burst_<notes>.jsonl)",
    )
    args = parser.parse_args()

    try:
        ctx = load_run_context(
            config_path=Path(args.config),
            profile_name=args.profile,
            target_key=args.target,
            target_url_override=args.target_url,
        )
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.output:
        output_path = Path(args.output)
    else:
        label = args.endpoint_notes.replace(",", "_").replace("=", "").replace(" ", "")
        output_path = default_output_path(TEST_ID, f"burst_{label}")

    return run_burst_submit_then_poll(
        ctx=ctx,
        test_id=TEST_ID,
        scenario=DEFAULT_SCENARIO,
        iterations=args.iterations,
        endpoint_notes=args.endpoint_notes,
        output_path=output_path,
    )


if __name__ == "__main__":
    raise SystemExit(main())
