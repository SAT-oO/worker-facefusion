RunPod End-to-End Test
======================

Spins up a local MinIO (S3-compatible) bucket and the worker container, then
exercises the full request/response cycle against the handler.

Prerequisites
-------------

- Docker with the `compose` plugin
- NVIDIA Container Toolkit (the worker runs on GPU)
- `curl`, `python3` on the host
- `ffprobe` (optional, used for media validation)

Run
---

```bash
bash tests/runpod/run_e2e.sh
```

First invocation builds the worker image (CUDA + pip deps + model pre-download),
which can take 10-30 minutes. Subsequent runs reuse the cache.

Set `KEEP_UP=1` to leave the stack running for manual poking:

```bash
KEEP_UP=1 bash tests/runpod/run_e2e.sh
# MinIO console: http://localhost:9001  (minioadmin / minioadmin)
# Worker API:    http://localhost:8000/runsync
docker compose -f tests/runpod/docker-compose.e2e.yml down -v
```

What it does
------------

1. Downloads `source.jpg` and `target-240p.mp4` from `facefusion-assets`
   (`examples-3.0.0`) into `fixtures/`, plus a base64-encoded copy.
2. Starts MinIO, creates bucket `ff-test`, uploads the target under
   `templates/target-240p.mp4`.
3. Builds and starts the worker pointed at the MinIO endpoint via env vars
   (`R2_ENDPOINT`, `R2_BUCKET`, `R2_PUBLIC_BASE_URL`, etc).
4. POSTs the payload in `sample_input.json` (with the base64 source spliced in)
   to `http://localhost:8000/runsync`.
5. Verifies the response contains `output_url`, `output_key`, `bucket`.
6. Downloads the result via the returned URL and runs `ffprobe` to confirm a
   video stream is present.

Switching to real Cloudflare R2
-------------------------------

In `docker-compose.e2e.yml`, replace the `worker` env block with real R2 creds:

```yaml
environment:
  R2_ACCOUNT_ID: <real>
  R2_ACCESS_KEY_ID: <real>
  R2_SECRET_ACCESS_KEY: <real>
  R2_BUCKET: <real-bucket>
  # leave R2_ENDPOINT and R2_PUBLIC_BASE_URL unset to fall back to
  # https://<account>.r2.cloudflarestorage.com and 24h presigned URLs
```

Remove the `minio` service and skip step 2/4 (upload the target to R2 once
manually). The rest of `run_e2e.sh` works unchanged.

Benchmark (RunPod serverless)
-----------------------------

For FlashBoot-aware cold-start and load testing against a live endpoint, see
[`benchmark/README.md`](benchmark/README.md).

Quick start:

```bash
cp tests/runpod/benchmark/config.example.json tests/runpod/benchmark/config.json
export RUNPOD_API_KEY=... ENDPOINT_ID=...
python3 tests/runpod/benchmark/run_benchmark.py --scenario cold_flashboot --profile fast_nvenc --target 120s
```
