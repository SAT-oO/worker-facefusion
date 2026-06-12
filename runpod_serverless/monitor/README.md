# RunPod endpoint health check

One script, no config file. Export credentials in your shell and run:

```bash
export RUNPOD_API_KEY=...
export ENDPOINT_ID=...

python3 runpod_serverless/monitor/check_once.py
```

Exit code `0` = healthy, `1` = unhealthy.

## Rules

| Condition | Result |
|-----------|--------|
| API unreachable or HTTP error | `UNHEALTHY` (critical) |
| `workers.unhealthy` > 3 | `UNHEALTHY` (critical) |
| Jobs in queue, no available workers (1st fetch) | waits `QUEUE_RECHECK_SECONDS` (default 10s), then fetches again |
| Jobs still queued with no workers after recheck | `UNHEALTHY` (critical) |
| Queue cleared after recheck (e.g. cold start) | no queue issue |
| `workers.throttled` > 5 | `UNHEALTHY` (critical) |

Override recheck wait:

```bash
export QUEUE_RECHECK_SECONDS=10
```

## Example output

**Healthy:**

```
====================================================
  RUNPOD HEALTH CHECK
====================================================
  Endpoint:  your-endpoint-id
  Status:    HEALTHY
----------------------------------------------------
  SNAPSHOT:
  jobs     inQueue=0  inProgress=0  failed=58
  workers  ready=6  idle=6  running=0
           throttled=4  unhealthy=0  initializing=0
====================================================
```

**Queue recheck (stderr while waiting):**

```
  Queue stalled on 1st fetch — waiting 10s before 2nd fetch...
```

**Unhealthy — queue still stalled after recheck:**

```
====================================================
  RUNPOD HEALTH CHECK
====================================================
  Endpoint:  your-endpoint-id
  Status:    UNHEALTHY
----------------------------------------------------
  FAILED CHECKS:
  [!!] CRITICAL  QUEUE_NO_WORKERS
       2 job(s) queued, 0 available workers (still stalled after 10s recheck)
----------------------------------------------------
  SNAPSHOT:
  jobs     inQueue=2  inProgress=0  failed=58
  workers  ready=0  idle=0  running=0
           throttled=4  unhealthy=0  initializing=0
====================================================
```

**Multiple failures** stack under `FAILED CHECKS:` in one report.
