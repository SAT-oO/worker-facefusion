RunPod Benchmark
================

Load and latency benchmarks for the FaceFusion serverless worker, designed
around **FlashBoot enabled** (production default).

FlashBoot: what it does
-----------------------

RunPod FlashBoot is two related mechanisms:

1. **Image pre-cache on the host** — what you see in idle-worker system logs
   ("docker image already downloaded"). RunPod keeps your endpoint image on the
   GPU host so scale-from-zero does not re-pull the full image every time.

2. **Process snapshot restore (CRIU-style)** — when a worker scales to zero
   after handling a job, RunPod can snapshot the running worker process,
   including Python heap and **GPU VRAM** (ONNX models already loaded). The
   next scale-up on the **same host + same image SHA** restores that snapshot
   instead of cold-booting Python and reloading models from disk.

FlashBoot does **not** bake extra state into the image at release/push time.
Snapshots are taken at **scale-down after a job**, scoped to `(host, image SHA)`.
A new image tag invalidates snapshots. Scheduling onto a **new host** pays the
full fresh-boot cost once (image may still be cached, but VRAM reload happens).

That is why benchmarks here measure two cold-start classes via `delayTime`:

| Class | Typical signal | Meaning |
|-------|----------------|---------|
| `flashboot_restore_or_warm` | `delayTime` ≤ 30s (configurable) | Snapshot restore or worker still warm |
| `fresh_boot_or_miss` | `delayTime` > 30s | New host, new image, or snapshot miss |

Endpoint settings (FlashBoot ON)
--------------------------------

Use these for realistic production cold-start testing:

| Setting | Recommended | Why |
|---------|-------------|-----|
| FlashBoot | **On** | Production behavior |
| Active workers (min) | **0** | Avoid permanently warm workers |
| Idle timeout | **60s** (or min allowed) | Scale down between cold-start samples |
| Max workers | **1** for latency A/B; **4–8** for soak | Isolate per-GPU latency vs throughput |
| Execution timeout | **≥ 600s** for 5-min stress clips | Avoid false failures |

For `cold_flashboot` runs, wait **idle_timeout + 30s** between jobs so the
worker fully scales down and a snapshot can be written before the next request.

Prerequisites
-------------

```bash
bash tests/runpod/fetch_fixtures.sh
cp tests/runpod/benchmark/config.example.json tests/runpod/benchmark/config.json
# Edit config.json: endpoint_id, target URLs (60s / 120s / 300s fixtures on R2)
export RUNPOD_API_KEY=...
export ENDPOINT_ID=...
```

Target URLs can also be passed via env vars referenced in config:

```bash
export BENCHMARK_TARGET_120S_URL="https://your-r2.../target_120s.mp4"
export BENCHMARK_TARGET_300S_URL="https://your-r2.../target_300s.mp4"
```

Run
---

```bash
# Cold start with FlashBoot (10 samples, 90s idle between jobs)
python3 tests/runpod/benchmark/run_benchmark.py \
  --scenario cold_flashboot \
  --profile fast_nvenc \
  --target 120s

# Warm steady-state (20 back-to-back jobs)
python3 tests/runpod/benchmark/run_benchmark.py \
  --scenario warm \
  --profile fast_nvenc \
  --target 120s

# Concurrent burst (4 parallel jobs)
python3 tests/runpod/benchmark/run_benchmark.py \
  --scenario concurrent \
  --profile fast_nvenc \
  --target 120s \
  --concurrency 4

# Compare baseline vs optimized profile on same fixture
python3 tests/runpod/benchmark/run_benchmark.py --scenario warm --profile baseline_e2e --target 120s
python3 tests/runpod/benchmark/run_benchmark.py --scenario warm --profile fast_nvenc --target 120s
```

Results land in `tests/runpod/benchmark/results/*.jsonl` plus a `.summary.json`
with p50/p90/p99 for `delayTime`, `executionTime`, and end-to-end wall time.

Analyze:

```bash
python3 tests/runpod/benchmark/analyze_results.py tests/runpod/benchmark/results/*.jsonl
```

Metrics
-------

| Metric | Source | Use |
|--------|--------|-----|
| `delayTime` | RunPod job status | Queue + cold start before handler runs |
| `executionTime` | RunPod job status | Handler wall clock |
| `total_time_ms` | Benchmark client | Submit → completed (includes polling overhead) |
| `timings.*` | Handler response | decode / download / facefusion / upload breakdown |

**SLA check (1–2 min video):** compare `delayTime + executionTime` p90 on
`fast_nvenc` + `120s` target under `cold_flashboot` vs `warm`.

Profiles
--------

| Profile | Purpose |
|---------|---------|
| `baseline_e2e` | Current quality-first settings (libx264 veryslow, 512 boost) |
| `fast_nvenc` | Latency target profile (CUDA, NVENC, 256 boost, 4 threads) |
| `fast_nvenc_threads8` | Same as fast_nvenc with 8 execution threads |

Suggested calibration order
---------------------------

1. `warm` + `120s` — sweep profiles (`baseline_e2e` vs `fast_nvenc`) for pure compute/encode cost.
2. `cold_flashboot` + `120s` + `fast_nvenc` — measure production cold-start SLA.
3. `warm` + `300s` — saturation / long-job stability on A4500.
4. `concurrent` — queue depth and whether to keep **1 job/GPU** and scale workers horizontally.
