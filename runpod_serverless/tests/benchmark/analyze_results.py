#!/usr/bin/env python3
"""Summarize benchmark JSONL result files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BENCHMARK_DIR = Path(__file__).resolve().parent
if str(_BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCHMARK_DIR))

from benchmark_common import load_records, summarize


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", nargs="+", help="JSONL result files")
    args = parser.parse_args()

    for path_str in args.results:
        path = Path(path_str)
        records = load_records(path)
        grouped: dict[tuple[str, str, str, str], list[dict]] = {}
        for record in records:
            key = (
                record.get("test_id", "unknown"),
                record.get("scenario", "unknown"),
                record.get("profile", "unknown"),
                record.get("target_key", "unknown"),
            )
            grouped.setdefault(key, []).append(record)

        print(f"\n=== {path} ===")
        for key, group in sorted(grouped.items()):
            test_id, scenario, profile, target = key
            summary = summarize(group)
            endpoint_notes = next((r.get("endpoint_notes") for r in group if r.get("endpoint_notes")), None)
            print(f"\n{test_id} / {scenario} / {profile} / {target}")
            if endpoint_notes:
                print(f"endpoint_notes: {endpoint_notes}")
            print(json.dumps(summary, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
