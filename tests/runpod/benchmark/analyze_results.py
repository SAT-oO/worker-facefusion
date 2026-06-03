#!/usr/bin/env python3
"""Summarize benchmark JSONL result files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((pct / 100) * (len(ordered) - 1)))))
    return ordered[index]


def load_records(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def summarize_group(records: list[dict]) -> dict:
    ok = [r for r in records if r.get("status") == "COMPLETED" and not r.get("error")]

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

    return {
        "samples": len(records),
        "success": len(ok),
        "failed": len(records) - len(ok),
        "delay_time_ms": {
            "p50": percentile(delay, 50),
            "p90": percentile(delay, 90),
            "mean": mean(delay) if delay else None,
        },
        "execution_time_ms": {
            "p50": percentile(execution, 50),
            "p90": percentile(execution, 90),
            "mean": mean(execution) if execution else None,
        },
        "total_time_ms": {
            "p50": percentile(total, 50),
            "p90": percentile(total, 90),
            "mean": mean(total) if total else None,
        },
        "handler_timings_ms_mean": {
            phase: mean(values) if values else None
            for phase, values in sorted(handler_phases.items())
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", nargs="+", help="JSONL result files")
    args = parser.parse_args()

    for path_str in args.results:
        path = Path(path_str)
        records = load_records(path)
        grouped: dict[tuple[str, str, str], list[dict]] = {}
        for record in records:
            key = (
                record.get("scenario", "unknown"),
                record.get("profile", "unknown"),
                record.get("target_key", "unknown"),
            )
            grouped.setdefault(key, []).append(record)

        print(f"\n=== {path} ===")
        for key, group in sorted(grouped.items()):
            scenario, profile, target = key
            summary = summarize_group(group)
            print(f"\n{scenario} / {profile} / {target}")
            print(json.dumps(summary, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
