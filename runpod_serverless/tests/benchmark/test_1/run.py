#!/usr/bin/env python3
"""
Test 1 — Production metrics (warm sequential load).

See test_1/README.md for methodology and RunPod console settings.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BENCHMARK_DIR = Path(__file__).resolve().parent.parent
if str(_BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCHMARK_DIR))

from benchmark_common import BENCHMARK_DIR, default_output_path, load_run_context, print_run_debug, run_sequential

TEST_ID = "test_1"
DEFAULT_PROFILE = "production"
DEFAULT_TARGET = "60s"
DEFAULT_ITERATIONS = 30
DEFAULT_SCENARIO = "warm_sequential"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test 1: production latency percentiles (N=30 warm sequential jobs)",
    )
    parser.add_argument("--config", default=str(BENCHMARK_DIR / "config.json"))
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--target-url", default=None, help="Override target video URL")
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--endpoint-notes", default=None, help="RunPod console settings, e.g. 'max_workers=1,min_workers=0'")
    parser.add_argument("--output", default=None, help="JSONL output path (default: results/test_1/<timestamp>_warm_production.jsonl)")
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

    print_run_debug(ctx)

    output_path = Path(args.output) if args.output else default_output_path(TEST_ID, "warm_production")

    return run_sequential(
        ctx=ctx,
        test_id=TEST_ID,
        scenario=DEFAULT_SCENARIO,
        iterations=args.iterations,
        endpoint_notes=args.endpoint_notes,
        output_path=output_path,
    )


if __name__ == "__main__":
    raise SystemExit(main())
