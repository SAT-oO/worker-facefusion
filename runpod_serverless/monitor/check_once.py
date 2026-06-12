#!/usr/bin/env python3
"""Fetch RunPod endpoint health once; print healthy or unhealthy to the terminal."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

RUNPOD_API = "https://api.runpod.ai/v2"
LINE = "=" * 52
RULE = "-" * 52

MAX_UNHEALTHY_WORKERS = 3
THROTTLED_ALERT_MIN = 5
QUEUE_RECHECK_SECONDS = 10.0 # Note: consecutive fetches are 10 seconds apart


@dataclass(frozen=True)
class Issue:
    level: str  # "warning" | "critical"
    code: str
    message: str


def fetch_health(endpoint_id: str, api_key: str, timeout: float = 30.0) -> dict[str, Any]:
    url = f"{RUNPOD_API}/{endpoint_id}/health"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def available_workers(workers: dict[str, int]) -> int:
    return sum(workers.get(k, 0) for k in ("ready", "idle", "running", "initializing"))


def is_queue_stalled(data: dict[str, Any]) -> bool:
    jobs = {k: int(v) for k, v in (data.get("jobs") or {}).items()}
    workers = {k: int(v) for k, v in (data.get("workers") or {}).items()}
    return jobs.get("inQueue", 0) > 0 and available_workers(workers) == 0


def evaluate(data: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    workers = {k: int(v) for k, v in (data.get("workers") or {}).items()}

    unhealthy = workers.get("unhealthy", 0)
    if unhealthy > MAX_UNHEALTHY_WORKERS:
        issues.append(
            Issue(
                "critical",
                "UNHEALTHY_WORKERS",
                f"{unhealthy} unhealthy workers (limit > {MAX_UNHEALTHY_WORKERS})",
            )
        )

    throttled = workers.get("throttled", 0)
    if throttled > THROTTLED_ALERT_MIN:
        issues.append(
            Issue(
                "critical",
                "WORKERS_THROTTLED",
                f"{throttled} throttled workers (limit > {THROTTLED_ALERT_MIN})",
            )
        )

    return issues


def queue_issue_after_recheck(data: dict[str, Any], wait_seconds: float) -> Issue | None:
    jobs = {k: int(v) for k, v in (data.get("jobs") or {}).items()}
    if not is_queue_stalled(data):
        return None
    in_queue = jobs.get("inQueue", 0)
    return Issue(
        "critical",
        "QUEUE_NO_WORKERS",
        (
            f"{in_queue} job(s) queued, 0 available workers "
            f"(still stalled after {wait_seconds:.0f}s recheck)"
        ),
    )


def format_snapshot(data: dict[str, Any] | None) -> list[str]:
    if not data:
        return ["  (no health data — API call failed)"]

    jobs = {k: int(v) for k, v in (data.get("jobs") or {}).items()}
    workers = {k: int(v) for k, v in (data.get("workers") or {}).items()}
    return [
        f"  jobs     inQueue={jobs.get('inQueue', 0)}  "
        f"inProgress={jobs.get('inProgress', 0)}  failed={jobs.get('failed', 0)}",
        f"  workers  ready={workers.get('ready', 0)}  idle={workers.get('idle', 0)}  "
        f"running={workers.get('running', 0)}",
        f"           throttled={workers.get('throttled', 0)}  "
        f"unhealthy={workers.get('unhealthy', 0)}  "
        f"initializing={workers.get('initializing', 0)}",
    ]


def issue_marker(level: str) -> str:
    return "!!" if level == "critical" else " !"


def print_report(
    *,
    endpoint_id: str,
    healthy: bool,
    issues: list[Issue],
    data: dict[str, Any] | None = None,
) -> None:
    status = "HEALTHY" if healthy else "UNHEALTHY"
    lines = [
        LINE,
        "  RUNPOD HEALTH CHECK",
        LINE,
        f"  Endpoint:  {endpoint_id}",
        f"  Status:    {status}",
    ]

    if issues:
        lines.append(RULE)
        lines.append("  FAILED CHECKS:")
        for issue in issues:
            lines.append(f"  [{issue_marker(issue.level)}] {issue.level.upper():8}  {issue.code}")
            lines.append(f"       {issue.message}")

    lines.append(RULE)
    lines.append("  SNAPSHOT:")
    lines.extend(format_snapshot(data))
    lines.append(LINE)

    print("\n".join(lines))


def print_config_error(missing: list[str]) -> None:
    print_report(
        endpoint_id="(not set)",
        healthy=False,
        issues=[
            Issue(
                "critical",
                "MISSING_CONFIG",
                f"export {' and '.join(missing)} in your shell before running",
            )
        ],
    )


def print_api_error(endpoint_id: str, code: str, message: str) -> None:
    print_report(
        endpoint_id=endpoint_id,
        healthy=False,
        issues=[Issue("critical", code, message)],
    )


def check_health(endpoint_id: str, api_key: str) -> tuple[dict[str, Any], list[Issue]]:
    data = fetch_health(endpoint_id, api_key)
    issues = evaluate(data)

    if not is_queue_stalled(data):
        return data, issues

    print(
        f"  Queue stalled on 1st fetch — waiting {QUEUE_RECHECK_SECONDS:.0f}s before 2nd fetch...",
        file=sys.stderr,
    )
    time.sleep(QUEUE_RECHECK_SECONDS)

    data = fetch_health(endpoint_id, api_key)
    issues = evaluate(data)

    queue_issue = queue_issue_after_recheck(data, QUEUE_RECHECK_SECONDS)
    if queue_issue:
        issues.append(queue_issue)

    return data, issues


def main() -> int:
    api_key = os.environ.get("RUNPOD_API_KEY", "").strip()
    endpoint_id = os.environ.get("ENDPOINT_ID", "").strip()

    missing = []
    if not api_key:
        missing.append("RUNPOD_API_KEY")
    if not endpoint_id:
        missing.append("ENDPOINT_ID")
    if missing:
        print_config_error(missing)
        return 1

    try:
        data, issues = check_health(endpoint_id, api_key)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print_api_error(endpoint_id, "API_HTTP_ERROR", f"HTTP {exc.code}: {body[:300]}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print_api_error(endpoint_id, "API_UNREACHABLE", str(exc))
        return 1

    if issues:
        print_report(endpoint_id=endpoint_id, healthy=False, issues=issues, data=data)
        return 1

    print_report(endpoint_id=endpoint_id, healthy=True, issues=[], data=data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
