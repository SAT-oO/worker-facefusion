# Test 2 — Horizontal scaling (burst queue delay)

Submit **N jobs at once**, then poll all in parallel. Compare **delay P99** across different RunPod `max_workers` settings.

## What it tests

- **Profile:** `production`
- **Clip:** `60s`
- **Pattern:** Submit 30 jobs immediately → poll all (measures queue head-of-line blocking)
- **Compare:** `delay_time_ms` P99 when `max_workers=1` vs `max_workers=10`

Execution time should stay flat; delay P99 should drop when max workers increases.

## RunPod console

Run **two separate phases**. Change `max_workers` in the console, wait ~2 min, then run again.

| Phase | Max workers | Purpose |
|-------|-------------|---------|
| A | **1** | Bottleneck baseline (high queue delay) |
| B | **10** | Scaled (reduced queue delay; 20 jobs still queue with a 30-job burst) |

Min workers **0** (production). First burst job may pay cold-start cost; compare phases using the same min workers. Pin GPU type if possible.

## Prerequisites

Same as Test 1 (`RUNPOD_API_KEY`, `ENDPOINT_ID`, fixtures).

## Run

**Phase A** (set max workers = 1 in console first):

```bash
python3 runpod_serverless/tests/benchmark/test_2/run.py \
  --endpoint-notes "max_workers=1,min_workers=0"
```

**Phase B** (set max workers = 10 in console, wait ~2 min):

```bash
python3 runpod_serverless/tests/benchmark/test_2/run.py \
  --endpoint-notes "max_workers=10,min_workers=0"
```

## Output

- `results/test_2/<timestamp>_burst_maxworkers1minworkers0.jsonl`
- `results/test_2/<timestamp>_burst_maxworkers10minworkers0.jsonl`
- Matching `.summary.json` files with `run_meta.endpoint_notes`

## Compare results

```bash
python3 runpod_serverless/tests/benchmark/analyze_results.py \
  runpod_serverless/tests/benchmark/results/test_2/*.jsonl
```

Focus on `delay_time_ms.p99` in each summary. Compare against Test 1 warm sequential delay (should be ~25 ms).

**`delay_class` labels** (per job): `warm` = picked up immediately; `queued` = waited in RunPod queue (burst test, not a cold boot). Ignore legacy `cold_start_class` in old result files.

## Source code

- Entry point: [`run.py`](run.py)
- Shared library: [`../benchmark_common.py`](../benchmark_common.py) (`run_burst_submit_then_poll`)
