# Test 1 — Production metrics

Measure production latency percentiles on a warm worker with sequential jobs.

## What it tests

- **Profile:** `production` (`submit_job.py` / `quality_tiers` tier 1, 8 threads, source-matched resolution/fps)
- **Clip:** `60s` (`target_video_4_1min.mp4`)
- **Pattern:** 30 back-to-back jobs, one at a time (no burst)
- **Metrics:** P50/P75/P90/P95/P99/mean for `delay_time_ms`, `execution_time_ms`, `total_time_ms`, and `facefusion_ms`

## RunPod console (before starting)

| Setting | Recommended |
|---------|-------------|
| FlashBoot | ON |
| Min workers | **0** (production setting; first job may cold-start — exclude from percentiles if needed) |
| Max workers | **1** (isolates per-job execution from queue delay) |
| Execution timeout | **360 s** |

## Prerequisites

```bash
export RUNPOD_API_KEY=...
export ENDPOINT_ID=...

bash runpod_serverless/tests/fetch_fixtures.sh
```

## Run

From repo root:

```bash
python3 runpod_serverless/tests/benchmark/test_1/run.py
```

Optional overrides:

```bash
python3 runpod_serverless/tests/benchmark/test_1/run.py \
  --iterations 30 \
  --endpoint-notes "max_workers=1,min_workers=0,gpu=RTX_A4500"
```

## Output

- `results/test_1/<timestamp>_warm_production.jsonl` — one record per job
- `results/test_1/<timestamp>_warm_production.summary.json` — percentiles + `run_meta`

## Pass criteria

- `success` = `iterations` (e.g. 30/30)
- Stable `execution_time_ms` P99 vs P50 on warm jobs (delay ~25 ms after worker is hot)
- `delay_class`: `warm` = worker ready; `cold_boot` = high delay on sequential submit (scale-from-zero)

## Source code

- Entry point: [`run.py`](run.py)
- Shared library: [`../benchmark_common.py`](../benchmark_common.py) (`run_sequential`)
