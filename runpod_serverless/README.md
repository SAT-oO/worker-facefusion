# RunPod Serverless — 集成与使用指南

> GitHub 浏览本目录时默认显示本文档（简体中文）。完整技术规格与运维说明见 **[MAINTENANCE_SPECIFICATION_CN.md](MAINTENANCE_SPECIFICATION_CN.md)**。English: [RUNPOD_SERVERLESS.md](RUNPOD_SERVERLESS.md)。

本目录为 FaceFusion 的 RunPod Serverless Worker：通过 RunPod API 接收换脸任务，在 GPU 上执行 `facefusion.py headless-run`，并将结果上传至 Cloudflare R2。


| 组件                    | 职责                                               |
| --------------------- | ------------------------------------------------ |
| **你的应用**              | 上传源人脸与目标视频到 R2，提交 RunPod 任务，轮询状态，返回 `output_url` |
| **RunPod Serverless** | 调度 GPU、排队、上报 `delayTime` / `executionTime`       |
| `**handler.py`**      | 解码输入、下载目标、运行 FaceFusion、上传输出                     |
| **R2**                | 输入/输出对象存储                                        |


入口：`runpod_serverless/handler.py` → `runpod.serverless.start({"handler": handler})`  
容器命令：`python3 -u runpod_serverless/handler.py`（见仓库根目录 `Dockerfile`）

---

## 1. 构建与部署 Worker

### 构建并推送镜像

```bash
# 在仓库根目录执行
docker build -t <registry>/worker-facefusion:<tag> .
docker push <registry>/worker-facefusion:<tag>
```

或拉取预构建镜像：

```bash
docker pull satoo869/worker-facefusion:latest
```

首次本地构建约 **10–30 分钟**（含模型下载）。镜像构建细节见 [技术规格 — 镜像构建](MAINTENANCE_SPECIFICATION_CN.md#image-build)。

### 创建 RunPod Serverless 端点

在 [RunPod 控制台](https://www.runpod.io/console/serverless) 配置：


| 配置项           | 建议值                                              |
| ------------- | ------------------------------------------------ |
| **容器镜像**      | 上述 `worker-facefusion` 镜像                        |
| **GPU**       | 支持 NVENC 的 NVIDIA GPU（`fast_nvenc` 等配置需要 NVENC）  |
| **容器磁盘**      | 按视频长度预留（建议约 100GB）                               |
| **FlashBoot** | **开启**（生产默认）                                     |
| **最小 Worker** | `0`（省成本）；需热机可设 `1+`                              |
| **最大 Worker** | 按并发需求                                            |
| **空闲超时**      | `60s` 或平台允许的最小值                                  |
| **执行超时**      | **360 s（6 分钟）**，与请求 `policy.executionTimeout` 一致 |


FlashBoot 与端点调优说明见 [技术规格 — FlashBoot](MAINTENANCE_SPECIFICATION_CN.md#flashboot)。

### 端点环境变量（R2）

在 RunPod 端点 **Environment Variables** 中配置（**不要**写在客户端请求里）：


| 变量                     | 必填  | 说明                                                                                                                            |
| ---------------------- | --- | ----------------------------------------------------------------------------------------------------------------------------- |
| `R2_ACCOUNT_ID`        | 是   | Cloudflare 账户 ID                                                                                                              |
| `R2_ACCESS_KEY_ID`     | 是   | R2 S3 Access Key，见 [Cloudflare 文档](https://developers.cloudflare.com/r2/api/tokens/#get-s3-api-credentials-from-an-api-token) |
| `R2_SECRET_ACCESS_KEY` | 是   | R2 Secret（API Token `value` 的 SHA-256）                                                                                        |
| `R2_BUCKET`            | 是   | 默认输出桶                                                                                                                         |
| `R2_PUBLIC_BASE_URL`   | 否   | 公共 CDN 根 URL；未设置则返回 24h 预签名 URL                                                                                               |
| `R2_ENDPOINT`          | 否   | 自定义 S3 端点（本地 MinIO 等）                                                                                                         |


### 应用侧 RunPod 凭证


| 变量               | 用途                                         |
| ---------------- | ------------------------------------------ |
| `RUNPOD_API_KEY` | `Authorization: Bearer …`                  |
| `ENDPOINT_ID`    | `https://api.runpod.ai/v2/{endpoint_id}/…` |


在 RunPod → Settings → API Keys 创建并写入密钥管理系统。

---

## 2. 应用集成（调用 Worker）

### API 端点

基础 URL：`https://api.runpod.ai/v2`


| 操作          | 方法     | 路径                               |
| ----------- | ------ | -------------------------------- |
| 提交任务        | `POST` | `/{endpoint_id}/run`             |
| 查询状态        | `GET`  | `/{endpoint_id}/status/{job_id}` |
| 本地同步（仅 e2e） | `POST` | `http://localhost:8000/runsync`  |


### 提交示例

**curl**

```bash
curl -sS -X POST "https://api.runpod.ai/v2/${ENDPOINT_ID}/run" \
  -H "Authorization: Bearer ${RUNPOD_API_KEY}" \
  -H "Content-Type: application/json" \
  -d @body.json
```

**Python（等价请求）** — 见 [`submit_job.py`](submit_job.py)：

```bash
export RUNPOD_API_KEY=... ENDPOINT_ID=...

# 使用与 curl 相同的 JSON 文件
python runpod_serverless/submit_job.py --body body.json

# 提交后轮询直至终态（对应下方「推荐集成流程」第 4 步）
python runpod_serverless/submit_job.py --body body.json --poll

# 或由本地文件构造 input（无需先写 body.json）
python runpod_serverless/submit_job.py \
  --source-image path/to/face.jpg \
  --target-url "https://<bucket>.<account>.r2.cloudflarestorage.com/templates/clip.mp4" \
  --poll
```

`body.json`：

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
  },
  "policy": {
    "executionTimeout": 360000,
    "ttl": 3600000,
    "lowPriority": false
  }
}
```

### 推荐集成流程

1. **源人脸** — 对用户人脸图做 Base64（PNG/JPEG），或服务端缓存编码结果。
2. **目标视频** — 提交任务前将模板上传到 R2，传入可访问的 HTTPS `target_url`。
3. **提交** — `POST /run`，携带 `input` 与可选 `policy`。
4. **轮询** — 每 2–5 秒 `GET /status/{id}` 直至 `COMPLETED` / `FAILED` / `TIMED_OUT` 等终态；客户端超时建议 ≥ **420 s**（含 6 分钟执行上限）。
5. **取结果** — `COMPLETED` 时读 `output.output_url`，并保存 `output_key` 以便后续管理。
6. **错误** — 失败时查看 `output.error`、`stderr`；详见 [技术规格 — 响应与排错](MAINTENANCE_SPECIFICATION_CN.md#api-response)。

### `input` 必填字段


| 字段                    | 必填  | 说明                                                                                             |
| --------------------- | --- | ---------------------------------------------------------------------------------------------- |
| `source_image_base64` | 是   | 源人脸 Base64                                                                                     |
| `target_url`          | 是   | 目标视频/图片 HTTPS URL                                                                              |
| `source_image_format` | 否   | 默认 `png`                                                                                       |
| `output_format`       | 否   | 默认 `mp4`                                                                                       |
| `processors`          | 否   | 默认 `["face_swapper"]`                                                                          |
| `face_swapper_model`  | 否   | 如 `inswapper_128_fp16`                                                                         |
| `extra_args`          | 否   | 传给 `headless-run` 的 CLI 参数；预设见 [技术规格 — 性能调优](MAINTENANCE_SPECIFICATION_CN.md#performance-tuning) |


`target_url` 须为 Worker 可下载的 R2/S3 或公开 HTTPS 地址；输出写入 `outputs/{uuid}.{format}`。URL 格式与输出桶规则见 [技术规格 — R2](MAINTENANCE_SPECIFICATION_CN.md#r2-storage)。

---

## 3. 本地开发与验证

### 冒烟测试（RunPod 约定）

```bash
# 编辑 runpod_serverless/test_input.json
python runpod_serverless/handler.py
```

### 完整 GPU 端到端（MinIO + Worker）

```bash
bash runpod_serverless/tests/run_e2e.sh
```

详见 [tests/README.md](tests/README.md)。

### 线上端点基准测试

```bash
bash runpod_serverless/tests/fetch_fixtures.sh
cp runpod_serverless/tests/benchmark/config.example.json runpod_serverless/tests/benchmark/config.json
export RUNPOD_API_KEY=... ENDPOINT_ID=...
python3 runpod_serverless/tests/benchmark/run_benchmark.py --scenario warm --profile fast_nvenc --target 120s
```

详见 [tests/benchmark/README.md](tests/benchmark/README.md)。

---

## 4. 参考


| 文档                                                             | 内容                                  |
| -------------------------------------------------------------- | ----------------------------------- |
| [MAINTENANCE_SPECIFICATION_CN.md](MAINTENANCE_SPECIFICATION_CN.md) | API 响应结构、环境变量全集、超时、运维、镜像构建、性能配置档    |
| [submit_job.py](submit_job.py)                                 | 提交任务 / 轮询状态的 Python 示例（等价于下方 curl） |
| [RUNPOD_SERVERLESS.md](RUNPOD_SERVERLESS.md)                   | English integration & specification |
| [test_input.json](test_input.json)                             | 本地 handler 示例载荷                     |
| [tests/sample_input.json](tests/sample_input.json)             | 含 `policy` / `extra_args` 的 e2e 示例  |


