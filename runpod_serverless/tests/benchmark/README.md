RunPod Benchmark
================

Benchmark harness for the FaceFusion serverless worker. Submits jobs to a live RunPod endpoint, polls until completion, and writes latency records to `results/`.

Prerequisites
-------------

- A deployed RunPod serverless endpoint (image + `R2_*` env vars). See [README.md](../../README.md) for setup; [TECHNICAL_SPECIFICATION_CN.md](../../TECHNICAL_SPECIFICATION_CN.md) for timeouts and profiles.
- `RUNPOD_API_KEY` and the endpoint ID.
- Target videos on R2 (or any URL the worker can download) — typically 60s / 120s / 300s clips for your SLA range.

One-time setup
--------------

```bash
# Source face fixture (base64) used in every job
bash runpod_serverless/tests/fetch_fixtures.sh

# Local config (gitignored if you copy from example)
cp runpod_serverless/tests/benchmark/config.example.json runpod_serverless/tests/benchmark/config.json
```

Edit `config.json`:

| Field | What to set |
| --- | --- |
| `endpoint_id` | RunPod serverless endpoint ID, or `${ENDPOINT_ID}` and export `ENDPOINT_ID` |
| `targets` | HTTPS URLs for each clip key (`60s`, `120s`, `300s`) |
| `fixtures.default_target_key` | Default `--target` when omitted (e.g. `120s`) |

Target URLs via environment (referenced in config as `${BENCHMARK_TARGET_120S_URL}` etc.):

```bash
export RUNPOD_API_KEY=...
export ENDPOINT_ID=...   # if using ${ENDPOINT_ID} in config
export BENCHMARK_TARGET_120S_URL="https://your-bucket.../target_120s.mp4"
export BENCHMARK_TARGET_300S_URL="https://your-bucket.../target_300s.mp4"
```

Run
---

From the repo root:

```bash
# Cold start: scale-down gap between jobs (default 90s idle, 10 iterations)
python3 runpod_serverless/tests/benchmark/run_benchmark.py \
  --scenario cold_flashboot \
  --profile fast_nvenc \
  --target 120s

# Warm: back-to-back jobs (no idle wait)
python3 runpod_serverless/tests/benchmark/run_benchmark.py \
  --scenario warm \
  --profile fast_nvenc \
  --target 120s

# Concurrent burst (default concurrency from config, often 4)
python3 runpod_serverless/tests/benchmark/run_benchmark.py \
  --scenario concurrent \
  --profile fast_nvenc \
  --target 120s

# Compare profiles on the same fixture
python3 runpod_serverless/tests/benchmark/run_benchmark.py --scenario warm --profile baseline_e2e --target 120s
python3 runpod_serverless/tests/benchmark/run_benchmark.py --scenario warm --profile fast_nvenc --target 120s
```

Useful overrides:

```bash
python3 runpod_serverless/tests/benchmark/run_benchmark.py \
  --scenario warm --profile fast_nvenc --target 120s \
  --target-url "https://..." \
  --iterations 5 \
  --wait-after-job 0 \
  --concurrency 2
```

| Flag | Purpose |
| --- | --- |
| `--config` | Path to config JSON (default: `config.json` in this directory) |
| `--scenario` | `cold_flashboot`, `warm`, or `concurrent` |
| `--profile` | Name from `config.profiles` (e.g. `fast_nvenc`) |
| `--target` | Key from `config.targets` (e.g. `120s`) |
| `--target-url` | Override target URL (skips `config.targets`) |
| `--iterations` | Override scenario iteration count |
| `--concurrency` | Parallel jobs per batch |
| `--wait-after-job` | Seconds to sleep between batches (cold scenario) |
| `--output` | Custom JSONL path |

Scenarios (in `config.json` → `scenarios`)
-------------------------------------------

| Scenario | Default behavior |
| --- | --- |
| `cold_flashboot` | 10 jobs, 90s wait between jobs, concurrency 1 |
| `warm` | 20 jobs, no wait, concurrency 1 |
| `concurrent` | 1 batch with concurrency 4 |

Profiles (in `config.json` → `profiles`)
----------------------------------------

| Profile | Purpose |
| --- | --- |
| `baseline_e2e` | Quality-first: libx264 veryslow, 512×512 pixel boost |
| `fast_nvenc` | Latency-oriented: CUDA, NVENC, 256×256 boost, 4 threads |
| `fast_nvenc_threads8` | Same as `fast_nvenc` with 8 threads |
| `sla_45s` | Aggressive SLA preset (see `extra_args` in config) |

Results
-------

Each run writes:

- `results/<timestamp>_<scenario>_<profile>.jsonl` — one JSON object per job
- `results/<timestamp>_<scenario>_<profile>.summary.json` — p50/p90/p99 for `delayTime`, `executionTime`, and client `total_time_ms`

Analyze across runs:

```bash
python3 runpod_serverless/tests/benchmark/analyze_results.py runpod_serverless/tests/benchmark/results/*.jsonl
```

Recorded fields (per job):

| Field | Source |
| --- | --- |
| `delay_time_ms` | RunPod status (`delayTime`) |
| `execution_time_ms` | RunPod status (`executionTime`) |
| `total_time_ms` | Client submit → completed |
| `handler_timings` | Handler `timings` (decode, download, facefusion, upload) |
| `output_url` | Handler output when successful |

Timeouts
--------

| Setting | Location | Default |
| --- | --- | --- |
| `policy.executionTimeout` | `config.json` → `base_input.policy` | 360000 ms (6 min) |
| `job_timeout_seconds` | `config.json` → `runpod` | 1800 s (client poll limit) |
| Endpoint execution timeout | RunPod console | **360 s** (6 min) |

If `execution_time_ms` is only a few seconds, the job failed early (bad `extra_args`, missing R2 env, bad target URL). Check `error` and `stderr_tail` in the JSONL line — not a timeout.

Suggested order
---------------

1. `warm` + `120s` — compare `baseline_e2e` vs `fast_nvenc` (compute/encode only).
2. `cold_flashboot` + `120s` + `fast_nvenc` — cold-start + idle gap between jobs.
3. `warm` + `300s` — longer clip stability.
4. `concurrent` — queue depth and multi-job behavior.
