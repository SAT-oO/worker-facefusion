# RunPod Serverless — Integration Guide

> Simplified Chinese: [README.md](README.md) (black-box) · [SETUP_INSTRUCTIONS_CN.md](SETUP_INSTRUCTIONS_CN.md) (setup) · [MAINTENANCE_SPECIFICATION_CN.md](MAINTENANCE_SPECIFICATION_CN.md) (spec & ops).

This document is the technical specification for internal teams wiring an existing application to the FaceFusion RunPod serverless worker in this repository.

The worker accepts a face-swap job over RunPod’s API, runs `facefusion.py headless-run` on GPU, and uploads the rendered video to Cloudflare R2 (or any S3-compatible store configured via env vars).


| Component             | Role                                                                                                                                        |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| **Your service**      | Owns user sessions, stores source faces, uploads target templates to R2, submits RunPod jobs, polls status, serves `output_url` to clients. |
| **RunPod serverless** | Schedules GPU workers, queues jobs, reports `delayTime` / `executionTime`.                                                                  |
| `**handler.py`**      | Decodes input, downloads target, runs FaceFusion, uploads result.                                                                           |
| **R2**                | Durable object storage for inputs and outputs.                                                                                              |


Entry point: `handler.py` → `runpod.serverless.start({"handler": handler})`.  
Container command: `python3 -u runpod_serverless/handler.py` (see repo root `Dockerfile`).

---

## RunPod endpoint setup

### 1. Build and push the worker image

Models are baked at image build time so cold starts do not download weights on first request.

```bash
docker build -t <registry>/worker-facefusion:v1.0 .
docker push <registry>/worker-facefusion:v1.0
```

You can also fetch the pre-built docker image below:

```bash
docker pull satoo869/worker-facefusion:v1.0
```

The build:

- Base: `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04`, Python 3.11, FFmpeg.
- Installs `requirements.txt` + `runpod_serverless/requirements-runpod.txt` and `onnxruntime-gpu==1.24.4`.
- Runs `tools/preload_face_swap_models.py` and verifies core ONNX files under `.assets/models/`.

First local build can take **10–30 minutes** (model download). CI/registry caches speed this up afterward.

### 2. Create a serverless endpoint

In the [RunPod console](https://www.runpod.io/console/serverless):


| Setting                  | Recommendation                                                                              | Notes                                                                                            |
| ------------------------ | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| **Container image**      | docker `worker-facefusion` image                                                            | Built from `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04` as base image                          |
| **GPU type**             | NVIDIA with NVENC (e.g. A4500 class, or runpod serverless defaults with 16GB and 24GB GPUs) | `h264_nvenc` in latency profiles requires NVENC.                                                 |
| **Container disk**       | Enough for temp frames during encode (eg. 100GB to be safe)                                 | Scale with target video length/resolution.                                                       |
| **FlashBoot**            | **On** (production default)                                                                 | Host image cache + optional process/VRAM snapshot after scale-down. See [FlashBoot](#flashboot). |
| **Active workers (min)** | `0` for cost; `1+` if you need an always-warm worker                                        | Min > 0 skips cold-start testing.                                                                |
| **Max workers**          | Match expected concurrency                                                                  | Often **1 job per GPU** for latency; scale horizontally for throughput.                          |
| **Idle timeout**         | `60s` (or minimum allowed)                                                                  | Worker scales down between jobs; affects cold-start benchmarks.                                  |
| **Execution timeout**    | **360 s** (6 min)                                                                           | Must cover longest expected face swap; align with request `policy.executionTimeout`.             |


### 3. Environment variables on the endpoint

Set these in the RunPod endpoint **Environment Variables** UI (not in your app’s request body):


| Variable               | Required | Description                                                                                                                                                                            |
| ---------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `R2_ACCOUNT_ID`        | Yes      | Cloudflare account ID. Used to build default endpoint `https://<id>.r2.cloudflarestorage.com` when `R2_ENDPOINT` is unset.                                                             |
| `R2_ACCESS_KEY_ID`     | Yes      | R2 S3-compatible access key ID, see [Cloudflare's official documentation](https://developers.cloudflare.com/r2/api/tokens/#get-s3-api-credentials-from-an-api-token) for more details. |
| `R2_SECRET_ACCESS_KEY` | Yes      | R2 S3-compatible secret. **Note: this is the SHA-256 hash of the API token, `value`.**                                                                                                 |
| `R2_BUCKET`            | Yes      | Default R2 bucket name for uploads when `target_url` does not imply another bucket.                                                                                                    |
| `R2_PUBLIC_BASE_URL`   | No       | Public CDN/custom domain base (no trailing slash). If set, `output_url` is `{base}/{key}`; otherwise a **24-hour presigned GET** URL is returned.                                      |
| `R2_ENDPOINT`          | No       | Override S3 API endpoint (e.g. MinIO in local e2e: `http://minio:9000`). Defaults to `https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com`.                                               |


For pure custom endpoints you still need access keys; `R2_ACCOUNT_ID` can be a placeholder when `R2_ENDPOINT` is fully specified (local MinIO uses `local`).

**Security:** Never embed R2 secrets in client requests. Only the worker container receives these variables.

### 4. API keys and endpoint ID -- RunPod

Your backend needs:


| Secret / config  | Where used                                                 |
| ---------------- | ---------------------------------------------------------- |
| `RUNPOD_API_KEY` | `Authorization: Bearer …` on RunPod REST calls             |
| `ENDPOINT_ID`    | Path segment in `https://api.runpod.ai/v2/{endpoint_id}/…` |


Create API keys in RunPod → Settings → API Keys. Store in your secrets manager; the benchmark harness reads them from the environment.

---

## FlashBoot

RunPod FlashBoot (when enabled) affects **cold start**, not the request schema:

1. **Image pre-cache** — Image stays on the GPU host after scale-to-zero (avoids full re-pull).
2. **Process snapshot** — After a job, RunPod may snapshot Python + loaded ONNX in VRAM; next job on the **same host + same image digest** restores faster.

Snapshots are **not** baked into the Docker image at push time. A new image tag invalidates snapshots; scheduling onto a new host pays a full boot once.

For production-like cold-start measurement, see `[runpod_serverless/tests/benchmark/README.md](runpod_serverless/tests/benchmark/README.md)`.

---

## Calling the worker from your application

### RunPod REST API

Base URL: `https://api.runpod.ai/v2`


| Operation         | Method | Path                             | Use                                                            |
| ----------------- | ------ | -------------------------------- | -------------------------------------------------------------- |
| Submit async job  | `POST` | `/{endpoint_id}/run`             | Production path: return `id` to client, poll status.           |
| Job status        | `GET`  | `/{endpoint_id}/status/{job_id}` | Poll until `COMPLETED`, `FAILED`, `CANCELLED`, or `TIMED_OUT`. |
| Sync (local only) | `POST` | `http://localhost:8000/runsync`  | Docker e2e stack; blocks until handler finishes.               |


**Submit example:**

```bash
curl -sS -X POST "https://api.runpod.ai/v2/${ENDPOINT_ID}/run" \
  -H "Authorization: Bearer ${RUNPOD_API_KEY}" \
  -H "Content-Type: application/json" \
  -d @body.json
```

`body.json` shape:

```json
{
  "input": { },
  "policy": {
    "executionTimeout": 360000,
    "ttl": 3600000,
    "lowPriority": false
  }
}
```

On success, status polling returns `output` as the handler’s return dict (see [Response schema](#response-schema-success)).

RunPod also exposes `delayTime` and `executionTime` (milliseconds) on the job status object—use these for SLA dashboards; the handler’s `timings` object breaks down work inside the container.

### Recommended integration flow

1. **Source face** — Your app already has the user’s face image. Base64-encode it (PNG or JPEG) for each job, or cache a stable encoding server-side.
2. **Target video** — Upload the template clip to R2 (or your bucket) *before* submitting the job. Pass an HTTPS URL the worker can read.
3. **Submit job** — `POST /run` with `input` (and optional `policy`).
4. **Poll** — `GET /status/{id}` every 2–5 s until terminal state. Client-side poll timeout should exceed queue wait plus **6 min** execution (e.g. **420 s** or use `job_timeout_seconds` in the benchmark config).
5. **Deliver result** — On `COMPLETED`, read `output.output_url` (or presigned URL). Persist `output_key` if you need to delete or re-sign later.
6. **Errors** — On `FAILED` or handler error fields, surface `error`, `stderr` tail, and `timings` to operators.

### R2 URL conventions for `target_url`

The handler downloads the target as follows:

- **R2 / S3-compatible URL** — Uses boto3 with configured credentials (`*.r2.cloudflarestorage.com`, or URL under `R2_PUBLIC_BASE_URL`).
- **Other HTTPS URLs** — Falls back to `requests` streaming download (public or signed GET).

Supported R2 path styles (see `_parse_r2_url` in `handler.py`):

- Virtual-hosted: `https://<bucket>.<account>.r2.cloudflarestorage.com/path/to/object.mp4`
- Path-style: `https://<account>.r2.cloudflarestorage.com/<bucket>/path/to/object.mp4`
- Public base: `https://your-cdn.example.com/path/to/object.mp4` (requires `R2_PUBLIC_BASE_URL` + `R2_BUCKET`)

**Output bucket:** If `target_url` resolves to a bucket, the output is uploaded to that bucket; otherwise `R2_BUCKET` is used. Output keys are always `outputs/{uuid}.{output_format}`.

---

## Request schema (`input`)

All fields live under `event.input` (RunPod wraps your payload in `event`).


| Field                 | Required | Default              | Description                                                                                       |
| --------------------- | -------- | -------------------- | ------------------------------------------------------------------------------------------------- |
| `source_image_base64` | **Yes**  | —                    | Base64-encoded source face image (PNG/JPEG).                                                      |
| `target_url`          | **Yes**  | —                    | HTTPS URL to the target video (or image) to process.                                              |
| `source_image_format` | No       | `png`                | File extension for decoded source: `png`, `jpg`, etc.                                             |
| `output_format`       | No       | `mp4`                | Output container/extension.                                                                       |
| `processors`          | No       | `["face_swapper"]`   | FaceFusion processor list passed to `--processors`.                                               |
| `face_swapper_model`  | No       | (FaceFusion default) | e.g. `inswapper_128_fp16` (baked in image).                                                       |
| `extra_args`          | No       | `[]`                 | Additional CLI tokens appended to `headless-run` (see [Performance tuning](#performance-tuning)). |


Minimal example:

```json
{
  "input": {
    "source_image_base64": "<base64>",
    "source_image_format": "jpg",
    "target_url": "https://<bucket>.<account>.r2.cloudflarestorage.com/templates/clip.mp4",
    "output_format": "mp4",
    "processors": ["face_swapper"],
    "face_swapper_model": "inswapper_128_fp16",
    "extra_args": []
  }
}
```

Reference payloads:

- This directory: `[test_input.json](test_input.json)` (RunPod local `python runpod_serverless/handler.py` convention).
- E2E: `[runpod_serverless/tests/sample_input.json](runpod_serverless/tests/sample_input.json)` (includes example `extra_args` and `policy`).

### Request-level `policy` (RunPod platform)

Optional sibling of `input` (not read by `handler.py`; enforced by RunPod):


| Field              | Example              | Purpose                                    |
| ------------------ | -------------------- | ------------------------------------------ |
| `executionTimeout` | `360000` (ms, 6 min) | Max time RunPod allows the handler to run. |
| `ttl`              | `3600000`            | Job time-to-live in queue.                 |
| `lowPriority`      | `false`              | Queue priority hint.                       |


Set `executionTimeout` to **360000 ms (6 min)** on the endpoint and in each request `policy`, matching the RunPod endpoint execution timeout.

---

## Response schema (success)

Handler return value (also appears as `output` on completed jobs):

```json
{
  "output_url": "https://.../outputs/abc123.mp4",
  "output_key": "outputs/abc123.mp4",
  "bucket": "your-bucket",
  "timings": {
    "decode_source_ms": 12,
    "download_target_ms": 450,
    "facefusion_ms": 85000,
    "upload_output_ms": 320,
    "handler_total_ms": 86200
  }
}
```


| Field        | Description                                                                |
| ------------ | -------------------------------------------------------------------------- |
| `output_url` | Public URL if `R2_PUBLIC_BASE_URL` is set; otherwise presigned GET (24 h). |
| `output_key` | Object key for housekeeping or generating new signed URLs.                 |
| `bucket`     | Bucket where the output was stored.                                        |
| `timings`    | Phase breakdown for observability.                                         |


---

## Response schema (failure)


| Shape                                                                                                                 | When                          |
| --------------------------------------------------------------------------------------------------------------------- | ----------------------------- |
| `{"error": "source_image_base64 is required"}`                                                                        | Validation                    |
| `{"error": "headless-run failed (exit N)", "stderr": "...", "stdout": "...", "diagnostics": {...}, "timings": {...}}` | FaceFusion subprocess failure |
| `{"error": "...", "traceback": "..."}`                                                                                | Unhandled exception           |


**Fast failures (~2 s `executionTime`)** usually mean misconfiguration (missing R2 env, bad `extra_args`, invalid target URL)—not a timeout. Inspect `stderr` and `error` before scaling timeouts.

---

## Performance tuning

FaceFusion CLI flags are passed through `extra_args` unchanged. Your app can expose a quality/latency preset by mapping to flag sets.

Benchmark profiles in `[runpod_serverless/tests/benchmark/config.example.json](runpod_serverless/tests/benchmark/config.example.json)`:


| Profile               | Intent                                                             |
| --------------------- | ------------------------------------------------------------------ |
| `baseline_e2e`        | Quality-first: `libx264`, `veryslow`, 512×512 pixel boost.         |
| `fast_nvenc`          | Latency target: CUDA, NVENC, 256×256 boost, 4 threads.             |
| `fast_nvenc_threads8` | Same with 8 execution threads.                                     |
| `sla_45s`             | Aggressive SLA: 128 boost, 0.5 scale, 15 fps cap, ultrafast NVENC. |


Example `fast_nvenc` flags (copy into `input.extra_args`):

```json
[
  "--execution-providers", "cuda",
  "--execution-thread-count", "4",
  "--video-memory-strategy", "tolerant",
  "--face-swapper-pixel-boost", "256x256",
  "--output-video-encoder", "h264_nvenc",
  "--output-video-preset", "fast",
  "--output-video-quality", "80"
]
```

Run `python facefusion.py headless-run --help` (or upstream docs) for the full flag list.

---

## Environment variables reference (all contexts)

### Worker container (RunPod endpoint / Docker)


| Variable               | Required | Description                                       |
| ---------------------- | -------- | ------------------------------------------------- |
| `R2_ACCOUNT_ID`        | Yes*     | Cloudflare account ID for default R2 endpoint.    |
| `R2_ACCESS_KEY_ID`     | Yes      | S3 access key.                                    |
| `R2_SECRET_ACCESS_KEY` | Yes      | S3 secret key.                                    |
| `R2_BUCKET`            | Yes      | Default output bucket.                            |
| `R2_PUBLIC_BASE_URL`   | No       | Public base URL for outputs (and R2 URL parsing). |
| `R2_ENDPOINT`          | No       | Custom S3 endpoint (MinIO, etc.).                 |


### Docker image build (optional overrides)

Used only during `tools/preload_face_swap_models.py` in `Dockerfile`:


| Variable                      | Default              | Description                                  |
| ----------------------------- | -------------------- | -------------------------------------------- |
| `FF_FACE_SWAPPER_MODEL`       | `inswapper_128_fp16` | Swapper ONNX to bake.                        |
| `FF_FACE_DETECTOR_MODEL`      | `yolo_face`          | Detector model id.                           |
| `FF_FACE_LANDMARKER_MODEL`    | `2dfan4`             | Landmarker model id.                         |
| `FF_FACE_OCCLUDER_MODEL`      | `xseg_1`             | Occluder model id.                           |
| `FF_FACE_PARSER_MODEL`        | `bisenet_resnet_34`  | Parser model id.                             |
| `FF_VOICE_EXTRACTOR_MODEL`    | `kim_vocal_2`        | Voice model (if preloaded).                  |
| `FF_DOWNLOAD_SCOPE`           | `lite`               | Download scope for preload.                  |
| `FF_PRELOAD_CONTENT_ANALYSER` | `0`                  | Set `1` to include content analyser in bake. |
| `FF_PRELOAD_FACE_CLASSIFIER`  | `0`                  | Set `1` to include face classifier.          |
| `FF_PRELOAD_VOICE_EXTRACTOR`  | `0`                  | Set `1` to include voice extractor.          |


### Your backend / CI (calling RunPod, benchmarks)


| Variable                    | Required       | Description                                          |
| --------------------------- | -------------- | ---------------------------------------------------- |
| `RUNPOD_API_KEY`            | For API calls  | RunPod API bearer token.                             |
| `ENDPOINT_ID`               | For API calls  | Serverless endpoint ID.                              |
| `BENCHMARK_TARGET_60S_URL`  | Benchmark only | R2 URL for 60 s fixture (see `config.example.json`). |
| `BENCHMARK_TARGET_120S_URL` | Benchmark only | R2 URL for 120 s fixture.                            |
| `BENCHMARK_TARGET_300S_URL` | Benchmark only | R2 URL for 300 s fixture.                            |


### Debugging (optional)


| Variable               | Description                                                                  |
| ---------------------- | ---------------------------------------------------------------------------- |
| `AGENT_DEBUG_LOG_PATH` | Extra path for agent debug NDJSON (default is dev-machine specific in code). |


At container start, `handler.py` logs Python/package versions, ONNX Runtime providers, and which `R2_`* vars are set (values redacted).

---

## Local development and verification

### Quick handler test (RunPod convention)

```bash
# test_input.json in this directory — edit target_url and source base64
python runpod_serverless/handler.py
```

### Full GPU e2e (MinIO + worker)

Requires Docker, NVIDIA Container Toolkit, GPU.

```bash
bash runpod_serverless/tests/run_e2e.sh
```

Details: `[runpod_serverless/tests/README.md](runpod_serverless/tests/README.md)`.

### Live endpoint benchmark

```bash
bash runpod_serverless/tests/fetch_fixtures.sh
cp runpod_serverless/tests/benchmark/config.example.json runpod_serverless/tests/benchmark/config.json
# Edit endpoint_id and target URLs
export RUNPOD_API_KEY=... ENDPOINT_ID=...
python3 runpod_serverless/tests/benchmark/run_benchmark.py --scenario warm --profile fast_nvenc --target 120s
```

Details: `[runpod_serverless/tests/benchmark/README.md](runpod_serverless/tests/benchmark/README.md)`.

---

## Timeouts checklist

Align these so long videos do not fail spuriously:


| Layer                             | Recommended                     |
| --------------------------------- | ------------------------------- |
| RunPod endpoint execution timeout | **360 s** (6 min)               |
| Request `policy.executionTimeout` | **360000** ms (6 min)           |
| Your status poll timeout          | **420 s** (7 min) or higher     |
| `target_url` HTTP download        | 300 s socket timeout in handler |


---

## Operational notes

- **Concurrency:** One heavy face-swap per GPU is typical; use max workers for parallel users, not multiple jobs on one GPU.
- **Idempotency:** Each job writes a new `outputs/{uuid}.`* key; your app should correlate RunPod `job_id` with user requests.
- **Source image size:** Large base64 payloads increase request size and `decode_source_ms`; consider reasonable max dimensions server-side.
- **Model changes:** Changing `face_swapper_model` or preload env vars requires an image rebuild so weights exist under `.assets/models/`.
- **Upstream FaceFusion:** CLI behavior follows [FaceFusion docs](https://docs.facefusion.io); this fork only adds the RunPod handler and R2 I/O layer.

---

## Related files


| Path                                                 | Purpose                           |
| ---------------------------------------------------- | --------------------------------- |
| `[handler.py](handler.py)`                           | Serverless handler implementation |
| `[Dockerfile](Dockerfile)`                           | Production image                  |
| `[requirements-runpod.txt](requirements-runpod.txt)` | RunPod worker Python deps         |
| `[test_input.json](test_input.json)`                 | Local handler smoke input         |
| `[tests/](tests/)`                                   | E2E and benchmark harness         |


