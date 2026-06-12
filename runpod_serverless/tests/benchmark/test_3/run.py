#!/usr/bin/env python3
"""
Test 3 — Soak / sustained stability.

See test_3/README.md for methodology.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BENCHMARK_DIR = Path(__file__).resolve().parent.parent
if str(_BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCHMARK_DIR))

from benchmark_common import BENCHMARK_DIR, default_output_path, load_run_context, run_soak

TEST_ID = "test_3"
DEFAULT_PROFILE = "production"
DEFAULT_TARGET = "60s"
DEFAULT_TOTAL_JOBS = 300
DEFAULT_RATE = 2.0
DEFAULT_MAX_INFLIGHT = 10
DEFAULT_WINDOW_SIZE = 50
DEFAULT_SCENARIO = "soak_stability"


def main() -> int:
    parser = argparse.ArgumentParser(description="Test 3: rate-limited soak test for stability")
    parser.add_argument("--config", default=str(BENCHMARK_DIR / "config.json"))
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--target-url", default=None, help="Override target video URL")
    parser.add_argument("--total-jobs", type=int, default=DEFAULT_TOTAL_JOBS)
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE, help="Submit rate (jobs per second)")
    parser.add_argument("--max-inflight", type=int, default=DEFAULT_MAX_INFLIGHT)
    parser.add_argument("--window-size", type=int, default=DEFAULT_WINDOW_SIZE)
    parser.add_argument("--endpoint-notes", default=None, help="e.g. 'max_workers=10,min_workers=0'")
    parser.add_argument("--output", default=None)
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

    output_path = Path(args.output) if args.output else default_output_path(TEST_ID, "soak_production")

    return run_soak(
        ctx=ctx,
        test_id=TEST_ID,
        scenario=DEFAULT_SCENARIO,
        total_jobs=args.total_jobs,
        rate=args.rate,
        max_inflight=args.max_inflight,
        window_size=args.window_size,
        endpoint_notes=args.endpoint_notes,
        output_path=output_path,
    )


if __name__ == "__main__":
    raise SystemExit(main())
