# Test 3 — Soak / sustained stability

Steady load over ~15–25 minutes to detect execution drift, failures, and OOM.

## What it tests

- **Profile:** `production`
- **Clip:** `60s`
- **Pattern:** **300 jobs** at **2 req/s**, up to **10 in-flight** at once
- **Metrics:** success rate, rolling execution P50/P99 (50-job windows), OOM count

## RunPod console

| Setting | Recommended |
|---------|-------------|
| FlashBoot | ON |
| Min workers | **0** (production) |
| Max workers | **10** (match `--max-inflight`) |
| Execution timeout | **360 s** |

## Run

```bash
export RUNPOD_API_KEY=... ENDPOINT_ID=...

python3 runpod_serverless/tests/benchmark/test_3/run.py \
  --endpoint-notes "max_workers=10,min_workers=0"
```

Defaults: `--total-jobs 300 --rate 2.0 --max-inflight 10 --window-size 50`

Shorter dry run:

```bash
python3 runpod_serverless/tests/benchmark/test_3/run.py \
  --total-jobs 60 --rate 2.0 --endpoint-notes "max_workers=10,min_workers=0"
```

## Output

- `results/test_3/<timestamp>_soak_production.jsonl` — appended per job (crash-safe)
- `results/test_3/<timestamp>_soak_production.summary.json` — includes `run_meta.rolling_windows`, `oom_count`, `execution_p99_degradation_ratio`

## Pass criteria

- `failed` = 0, `oom_count` = 0
- `execution_p99_degradation_ratio` ≤ 1.25 (last window vs first)

## Source code

- Entry point: [`run.py`](run.py)
- Shared library: [`../benchmark_common.py`](../benchmark_common.py) (`run_soak`)
