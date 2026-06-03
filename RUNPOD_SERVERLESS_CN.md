# RunPod Serverless — 集成接口文档

本文档为内部团队将现有应用接入本仓库 FaceFusion RunPod Serverless Worker 的技术规格说明。英文版见 [RUNPOD_SERVERLESS.md](RUNPOD_SERVERLESS.md)。

Worker 通过 RunPod API 接收换脸任务，在 GPU 上执行 `facefusion.py headless-run`，并将渲染后的视频上传至 Cloudflare R2（或通过环境变量配置的任意 S3 兼容存储）。


| 组件                    | 职责                                                              |
| --------------------- | --------------------------------------------------------------- |
| **前端 app**            | 管理用户会话、存储源人脸、将目标模板上传至 R2、提交 RunPod 任务、轮询状态、向客户端返回 `output_url`。 |
| **RunPod Serverless** | 调度 GPU Worker、排队任务、上报 `delayTime` / `executionTime`。            |
| `**handler.py`**      | 解码输入、下载目标素材、运行 FaceFusion、上传结果。                                 |
| **R2**                | 输入/输出的持久化对象存储。                                                  |


app 指令入口：`handler.py` → `runpod.serverless.start({"handler": handler})`。  
容器启动命令：`python3 -u handler.py`（见 `Dockerfile`）。

---

## RunPod 端点配置

### 1. 构建并推送 Worker 镜像

模型在镜像构建阶段预置（bake），避免冷启动时首次请求再下载权重。

```bash
docker build -t <registry>/worker-facefusion:<tag> .
docker push <registry>/worker-facefusion:<tag>
```

也可直接拉取预构建镜像：

```bash
docker pull satoo869/worker-facefusion:latest
```

构建说明：

- 基础镜像：`nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04`，Python 3.11，FFmpeg。
- 安装 `requirements.txt` + `requirements-runpod.txt` 以及 `onnxruntime-gpu==1.24.4`。
- 执行 `tools/preload_face_swap_models.py`，并校验 `.assets/models/` 下的核心 ONNX 文件。

首次本地构建约需 **10–30 分钟**（含模型下载）；之后可由 CI/镜像仓库缓存加速。

### 2. 创建 Serverless 端点

在 [RunPod 控制台](https://www.runpod.io/console/serverless) 中配置：


| 配置项                                 | 建议值                                                                   | 说明                                                   |
| ----------------------------------- | --------------------------------------------------------------------- | ---------------------------------------------------- |
| **容器镜像（Container image）**           | `worker-facefusion` 镜像                                                | 基于 `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04` 构建 |
| **GPU 类型**                          | 支持 NVENC 的 NVIDIA GPU（如 A4500 档位，或 RunPod Serverless 默认 16GB/24GB 显卡） | 延迟优化配置中的 `h264_nvenc` 依赖 NVENC 硬件编码                  |
| **容器磁盘（Container disk）**            | 编码临时帧所需空间（例如 100GB 以便有余量）                                             | 随目标视频时长/分辨率调整                                        |
| **FlashBoot**                       | **开启**（生产默认）                                                          | 主机镜像缓存 + 缩容后可选的进程/显存快照。见 [FlashBoot](#flashboot)     |
| **最小活跃 Worker（Active workers min）** | `0` 节省成本；需常驻热机则 `1+`                                                  | min > 0 时无法有效测试冷启动                                   |
| **最大 Worker（Max workers）**          | 按预期并发设置                                                               | 延迟场景通常 **每 GPU 一个任务**；吞吐靠横向扩容                        |
| **空闲超时（Idle timeout）**              | `60s`（或平台允许的最小值）                                                      | 任务间隔缩容；影响冷启动基准测试                                     |
| **执行超时（Execution timeout）**         | **360 s（6 分钟）**                                                       | 须覆盖最长换脸耗时；与请求 `policy.executionTimeout` 一致           |


### 3. 端点环境变量

在 RunPod 端点 **Environment Variables** 界面配置（不要写在应用请求体中）：


| 变量                     | 是否必填 | 说明                                                                                                                                      |
| ---------------------- | ---- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `R2_ACCOUNT_ID`        | 是    | Cloudflare 账户 ID。未设置 `R2_ENDPOINT` 时用于构造默认端点 `https://<id>.r2.cloudflarestorage.com`。                                                   |
| `R2_ACCESS_KEY_ID`     | 是    | R2 S3 兼容 Access Key ID，详见 [Cloudflare 官方文档](https://developers.cloudflare.com/r2/api/tokens/#get-s3-api-credentials-from-an-api-token)。 |
| `R2_SECRET_ACCESS_KEY` | 是    | R2 S3 兼容 Secret。**注意：此处为 API Token 的 `value` 字段经 SHA-256 哈希后的值。**                                                                       |
| `R2_BUCKET`            | 是    | 当 `target_url` 未指定其他存储桶时，用于上传的默认 R2 桶名。                                                                                                 |
| `R2_PUBLIC_BASE_URL`   | 否    | 公共 CDN/自定义域名根 URL（末尾不要 `/`）。设置后 `output_url` 为 `{base}/{key}`；否则返回 **24 小时有效** 的预签名 GET URL。                                            |
| `R2_ENDPOINT`          | 否    | 覆盖 S3 API 端点（如本地 e2e 的 MinIO：`http://minio:9000`）。默认 `https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com`。                                |


使用完全自定义端点时仍需要访问密钥；若已完整指定 `R2_ENDPOINT`，`R2_ACCOUNT_ID` 可为占位值（本地 MinIO 常用 `local`）。

**安全：** 切勿在客户端请求中嵌入 R2 密钥，仅 Worker 容器应持有上述环境变量。

### 4. API 密钥与端点 ID（RunPod）

后端需要：


| 密钥/配置            | 用途                                                |
| ---------------- | ------------------------------------------------- |
| `RUNPOD_API_KEY` | RunPod REST API：`Authorization: Bearer …`         |
| `ENDPOINT_ID`    | URL 路径：`https://api.runpod.ai/v2/{endpoint_id}/…` |


在 RunPod → Settings → API Keys 创建密钥，存入密钥管理系统；基准测试脚本从环境变量读取。

---

## FlashBoot

启用 RunPod FlashBoot 时影响 **冷启动**，不改变请求结构：

1. **镜像预缓存（Image pre-cache）** — 缩容至零后镜像仍保留在 GPU 主机，避免每次完整拉取。
2. **进程快照（Process snapshot）** — 任务结束后 RunPod 可能对已加载的 Python + ONNX（显存 VRAM）做快照；下次在 **同一主机 + 同一镜像摘要（digest）** 上恢复更快。

快照 **不会** 在 `docker push` 时写入镜像；新镜像标签会使快照失效；调度到新主机仍会完整冷启动一次。

生产级冷启动测量见 `[tests/runpod/benchmark/README.md](tests/runpod/benchmark/README.md)`。

---

## 从应用调用 Worker

### RunPod REST API

基础 URL：`https://api.runpod.ai/v2`


| 操作        | 方法     | 路径                               | 用途                                                  |
| --------- | ------ | -------------------------------- | --------------------------------------------------- |
| 提交异步任务    | `POST` | `/{endpoint_id}/run`             | 生产路径：返回 `id`，客户端轮询状态                                |
| 任务状态      | `GET`  | `/{endpoint_id}/status/{job_id}` | 轮询直至 `COMPLETED`、`FAILED`、`CANCELLED` 或 `TIMED_OUT` |
| 同步调用（仅本地） | `POST` | `http://localhost:8000/runsync`  | Docker e2e 栈；阻塞直至 handler 结束                        |


**请求示例：**

```bash
curl -sS -X POST "https://api.runpod.ai/v2/${ENDPOINT_ID}/run" \
  -H "Authorization: Bearer ${RUNPOD_API_KEY}" \
  -H "Content-Type: application/json" \
  -d @body.json
```

`body.json` 结构：

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

成功后，状态轮询的 `output` 即为 handler 返回值（见 [成功响应结构](#成功响应结构)）。

RunPod 在任务状态对象上提供 `delayTime`、`executionTime`（毫秒），可用于 SLA 看板；handler 的 `timings` 字段分解容器内各阶段耗时。

### 推荐集成流程

1. **原人脸（Source face）** — 应用侧已有用户人脸图。每任务 Base64 编码（PNG/JPEG），或在服务端缓存稳定编码。
2. **目标视频（Target video）** — 提交任务 **前** 将模板片段上传至 R2（或你的桶），传入 Worker 可访问的 HTTPS URL。
3. **提交任务** — `POST /run`，携带 `input`（及可选 `policy`）。
4. **轮询** — 每 2–5 秒 `GET /status/{id}` 直至终态。客户端轮询超时应大于排队等待 + **6 分钟** 执行时间（例如 **420 秒**，或使用基准配置中的 `job_timeout_seconds`）。
5. **交付结果** — `COMPLETED` 时读取 `output.output_url`（或预签名 URL）。若需删除或重新签名，请持久化 `output_key`。
6. **错误处理** — `FAILED` 或 handler 错误字段时，向运维暴露 `error`、`stderr` 尾部及 `timings`。

### `target_url` 的 R2 URL 约定

Handler 下载目标素材的逻辑：

- **R2 / S3 兼容 URL** — 使用 boto3 与已配置凭证（`*.r2.cloudflarestorage.com`，或 `R2_PUBLIC_BASE_URL` 下的 URL）。
- **其他 HTTPS URL** — 回退为 `requests` 流式下载（公开或带签名的 GET）。

支持的 R2 路径风格（实现见 `handler.py` 中 `_parse_r2_url`）：

- 虚拟主机风格（Virtual-hosted）：`https://<bucket>.<account>.r2.cloudflarestorage.com/path/to/object.mp4`
- 路径风格（Path-style）：`https://<account>.r2.cloudflarestorage.com/<bucket>/path/to/object.mp4`
- 公共域名：`https://your-cdn.example.com/path/to/object.mp4`（需配置 `R2_PUBLIC_BASE_URL` + `R2_BUCKET`）

**输出桶：** 若 `target_url` 能解析出桶名，则输出上传到该桶；否则使用 `R2_BUCKET`。输出对象键恒为 `outputs/{uuid}.{output_format}`。

---

## `curl`请求结构（`input`）

所有业务字段位于 `event.input`（RunPod 将你的载荷包装在 `event` 中）。


| 字段                    | 必填    | 默认值                | 说明                                           |
| --------------------- | ----- | ------------------ | -------------------------------------------- |
| `source_image_base64` | **是** | —                  | Base64 编码的源人脸图像（PNG/JPEG）                    |
| `target_url`          | **是** | —                  | 待处理目标视频（或图片）的 HTTPS URL                      |
| `source_image_format` | 否     | `png`              | 解码后源文件扩展名：`png`、`jpg` 等                      |
| `output_format`       | 否     | `mp4`              | 输出容器/扩展名                                     |
| `processors`          | 否     | `["face_swapper"]` | 传给 `--processors` 的 FaceFusion 处理器列表         |
| `face_swapper_model`  | 否     | （FaceFusion 默认）    | 如 `inswapper_128_fp16`（已烘焙进镜像）               |
| `extra_args`          | 否     | `[]`               | 追加到 `headless-run` 的 CLI 参数（见 [性能调优](#性能调优)） |


最小示例：

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

参考载荷：

- 仓库根目录：[test_input.json](test_input.json)（RunPod 本地 `python handler.py` 约定）
- 端到端：[tests/runpod/sample_input.json](tests/runpod/sample_input.json)（含 `extra_args`、`policy` 示例）

### 请求级 `policy`（RunPod 平台）

与 `input` 同级，可选；**不由 `handler.py` 读取**，由 RunPod 平台强制执行：


| 字段                 | 示例                | 含义                        |
| ------------------ | ----------------- | ------------------------- |
| `executionTimeout` | `360000`（毫秒，6 分钟） | RunPod 允许 handler 运行的最长时间 |
| `ttl`              | `3600000`         | 任务在队列中的存活时间（TTL）          |
| `lowPriority`      | `false`           | 低优先级队列提示                  |


请在 RunPod 端点与每次请求的 `policy` 中将 `executionTimeout` 设为 **360000 ms（6 分钟）**，并与端点执行超时一致。

---

## 成功响应结构

Handler 返回值（已完成任务的 `output` 字段内容相同）：

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


| 字段           | 说明                                                        |
| ------------ | --------------------------------------------------------- |
| `output_url` | 若设置 `R2_PUBLIC_BASE_URL` 则为公共 URL；否则为 24 小时有效的预签名 GET URL |
| `output_key` | 对象键，用于运维或重新生成签名 URL                                       |
| `bucket`     | 输出所在存储桶                                                   |
| `timings`    | 各阶段耗时，便于可观测性                                              |


---

## 失败响应结构


| 响应形态                                                                                                                  | 场景               |
| --------------------------------------------------------------------------------------------------------------------- | ---------------- |
| `{"error": "source_image_base64 is required"}`                                                                        | 参数校验失败           |
| `{"error": "headless-run failed (exit N)", "stderr": "...", "stdout": "...", "diagnostics": {...}, "timings": {...}}` | FaceFusion 子进程失败 |
| `{"error": "...", "traceback": "..."}`                                                                                | 未捕获异常            |


**快速失败（`executionTime` 约 2 秒）** 通常表示配置错误（缺少 R2 环境变量、`extra_args` 非法、`target_url` 无效等），**而非**执行超时。请先查看 `stderr` 与 `error`，再调整超时配置。

---

## 性能调优

FaceFusion CLI 参数经 `extra_args` 原样透传。应用可将画质/延迟预设映射为不同参数组。

基准配置档见 `[tests/runpod/benchmark/config.example.json](tests/runpod/benchmark/config.example.json)`：


| 配置档（Profile）          | 定位                                                |
| --------------------- | ------------------------------------------------- |
| `baseline_e2e`        | 画质优先：`libx264`、`veryslow`、512×512 pixel boost     |
| `fast_nvenc`          | 延迟目标：CUDA、NVENC、256×256 boost、4 线程                |
| `fast_nvenc_threads8` | 同上，8 个执行线程                                        |
| `sla_45s`             | 激进 SLA：128 boost、0.5 缩放、15 fps 上限、NVENC ultrafast |


`fast_nvenc` 示例（复制到 `input.extra_args`）：

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

完整参数列表：`python facefusion.py headless-run --help` 或 [FaceFusion 文档](https://docs.facefusion.io)。

---

## 环境变量一览

### Worker 容器（RunPod 端点 / Docker）


| 变量                     | 必填  | 说明                          |
| ---------------------- | --- | --------------------------- |
| `R2_ACCOUNT_ID`        | 是*  | Cloudflare 账户 ID，用于默认 R2 端点 |
| `R2_ACCESS_KEY_ID`     | 是   | S3 Access Key               |
| `R2_SECRET_ACCESS_KEY` | 是   | S3 Secret Key               |
| `R2_BUCKET`            | 是   | 默认输出桶                       |
| `R2_PUBLIC_BASE_URL`   | 否   | 输出的公共 URL 根路径（及 R2 URL 解析）  |
| `R2_ENDPOINT`          | 否   | 自定义 S3 端点（MinIO 等）          |


### Docker 镜像构建（可选覆盖）

仅用于 `Dockerfile` 中的 `tools/preload_face_swap_models.py`：


| 变量                            | 默认值                  | 说明                |
| ----------------------------- | -------------------- | ----------------- |
| `FF_FACE_SWAPPER_MODEL`       | `inswapper_128_fp16` | 预烘焙的换脸 ONNX 模型    |
| `FF_FACE_DETECTOR_MODEL`      | `yolo_face`          | 人脸检测模型 ID         |
| `FF_FACE_LANDMARKER_MODEL`    | `2dfan4`             | 人脸关键点模型 ID        |
| `FF_FACE_OCCLUDER_MODEL`      | `xseg_1`             | 遮挡模型 ID           |
| `FF_FACE_PARSER_MODEL`        | `bisenet_resnet_34`  | 解析模型 ID           |
| `FF_VOICE_EXTRACTOR_MODEL`    | `kim_vocal_2`        | 人声分离模型（若预加载）      |
| `FF_DOWNLOAD_SCOPE`           | `lite`               | 预下载范围             |
| `FF_PRELOAD_CONTENT_ANALYSER` | `0`                  | 设为 `1` 则预加载内容分析模块 |
| `FF_PRELOAD_FACE_CLASSIFIER`  | `0`                  | 设为 `1` 则预加载人脸分类器  |
| `FF_PRELOAD_VOICE_EXTRACTOR`  | `0`                  | 设为 `1` 则预加载人声提取器  |


### 后端 / CI（调用 RunPod、基准测试）


| 变量                          | 必填         | 说明                   |
| --------------------------- | ---------- | -------------------- |
| `RUNPOD_API_KEY`            | 调用 API 时需要 | RunPod API Bearer 令牌 |
| `ENDPOINT_ID`               | 调用 API 时需要 | Serverless 端点 ID     |
| `BENCHMARK_TARGET_60S_URL`  | 仅基准测试      | 60 秒测试视频的 R2 URL     |
| `BENCHMARK_TARGET_120S_URL` | 仅基准测试      | 120 秒测试视频的 R2 URL    |
| `BENCHMARK_TARGET_300S_URL` | 仅基准测试      | 300 秒测试视频的 R2 URL    |


### 调试（可选）


| 变量                     | 说明                                    |
| ---------------------- | ------------------------------------- |
| `AGENT_DEBUG_LOG_PATH` | Agent 调试 NDJSON 的额外写入路径（代码内另有开发机默认路径） |


容器启动时，`handler.py` 会打印 Python/依赖版本、ONNX Runtime 执行提供程序（execution providers），以及哪些 `R2_`* 变量已设置（不打印具体值）。

---

## 本地开发与验证

### 快速 Handler 测试（RunPod 约定）

```bash
# 编辑仓库根目录 test_input.json 中的 target_url 与 source base64
python handler.py
```

### 完整 GPU 端到端（MinIO + Worker）

需要 Docker、NVIDIA Container Toolkit、GPU。

```bash
bash tests/runpod/run_e2e.sh
```

详见 `[tests/runpod/README.md](tests/runpod/README.md)`。

### 线上端点基准测试

```bash
bash tests/runpod/fetch_fixtures.sh
cp tests/runpod/benchmark/config.example.json tests/runpod/benchmark/config.json
# 编辑 endpoint_id 与 target URL
export RUNPOD_API_KEY=... ENDPOINT_ID=...
python3 tests/runpod/benchmark/run_benchmark.py --scenario warm --profile fast_nvenc --target 120s
```

详见 `[tests/runpod/benchmark/README.md](tests/runpod/benchmark/README.md)`。

---

## 超时配置清单

请对齐以下项，避免长视频被误杀：


| 层级                           | 建议值                       |
| ---------------------------- | ------------------------- |
| RunPod 端点执行超时                | **360 s（6 分钟）**           |
| 请求 `policy.executionTimeout` | **360000 ms（6 分钟）**       |
| 客户端状态轮询超时                    | **420 s（7 分钟）** 或更长       |
| `target_url` HTTP 下载         | Handler 内 socket 超时 300 s |


---

## 运维说明

- **并发：** 通常每 GPU 同时跑一个重负载换脸任务；并行用户靠增加 Worker 数量，而非单 GPU 多任务。
- **幂等性：** 每次任务写入新的 `outputs/{uuid}.`* 键；应用应将 RunPod `job_id` 与用户请求关联。
- **源图大小：** 过大的 Base64 会增加请求体与 `decode_source_ms`；建议在服务端限制分辨率。
- **模型变更：** 修改 `face_swapper_model` 或预加载相关环境变量后须 **重新构建镜像**，确保 `.assets/models/` 中有对应权重。
- **上游 FaceFusion：** CLI 行为以 [FaceFusion 文档](https://docs.facefusion.io) 为准；本 Fork 仅增加 RunPod Handler 与 R2 读写层。

---

## 相关文件


| 路径                                                   | 用途                      |
| ---------------------------------------------------- | ----------------------- |
| `[handler.py](handler.py)`                           | Serverless Handler 实现   |
| `[Dockerfile](Dockerfile)`                           | 生产镜像                    |
| `[requirements-runpod.txt](requirements-runpod.txt)` | RunPod Worker Python 依赖 |
| `[test_input.json](test_input.json)`                 | 本地 Handler 冒烟输入         |
| `[tests/runpod/](tests/runpod/)`                     | 端到端与基准测试工具              |


