# RunPod Serverless — 技术规格与运维手册

> 集成与快速上手见 [README.md](README.md)。English: [RUNPOD_SERVERLESS.md](RUNPOD_SERVERLESS.md)。

本文档描述 Worker 行为契约、存储约定、性能调优、环境变量、超时策略及日常运维，供维护者与深度集成方查阅。

---

## 系统架构

Worker 通过 RunPod API 接收换脸任务，在 GPU 上执行 `facefusion.py headless-run`，并将渲染结果上传至 Cloudflare R2（或任意通过环境变量配置的 S3 兼容存储）。


| 组件                       | 职责                                                             |
| ------------------------ | -------------------------------------------------------------- |
| **前端 / 业务应用**            | 管理用户会话、存储源人脸、将目标模板上传至 R2、提交 RunPod 任务、轮询状态、向客户端返回 `output_url` |
| **RunPod Serverless**    | 调度 GPU Worker、排队任务、上报 `delayTime` / `executionTime`            |
| `**handler.py`**         | 解码输入、下载目标、运行 FaceFusion、上传结果                                   |
| **Cloudflare R2 Bucket** | 输入/输出的持久化对象存储                                                  |


实现入口：`runpod_serverless/handler.py` → `runpod.serverless.start({"handler": handler})`。  
Handler 通过 `_REPO_ROOT` 定位仓库根目录下的 `facefusion.py` 与 `.assets/models/`（容器内 `WORKDIR` 为 `/app`）。

---



## Runpod Serverless FlashBoot

启用 RunPod FlashBoot 时影响 **冷启动**，不改变请求 JSON 结构：

1. **镜像预缓存（Image pre-cache）** — 缩容至零后镜像仍保留在 GPU 主机，避免每次完整拉取。
2. **进程快照（Process snapshot）** — 任务结束后 RunPod 可能对已加载的 Python + ONNX（显存 VRAM）做快照；下次在 **同一主机 + 同一镜像摘要（digest）** 上恢复更快。

快照 **不会** 在 `docker push` 时写入镜像；新镜像标签会使快照失效；调度到新主机仍会完整冷启动一次。

生产级冷启动测量见 [tests/benchmark/README.md](tests/benchmark/README.md)。

### 端点参数参考（运维调优）


| 配置项                      | 建议值                                               | 说明                                                |
| ------------------------ | ------------------------------------------------- | ------------------------------------------------- |
| **容器镜像**                 | `worker-facefusion`                               | 基于 `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04` |
| **GPU 类型**               | 支持 NVENC 的 NVIDIA（如 A4500，或 RunPod 16GB/24GB 默认卡） | `h264_nvenc` 依赖 NVENC                             |
| **容器磁盘**                 | 编码临时帧空间（建议约 100GB）                                | 随目标视频时长/分辨率调整                                     |
| **FlashBoot**            | **开启**                                            | 生产默认                                              |
| **Active workers (min)** | `0` 或 `1+`                                        | min > 0 时难以测冷启动                                   |
| **Max workers**          | 按并发                                               | 通常 **每 GPU 一个重任务**                                |
| **Idle timeout**         | `60s` 或最小允许值                                      | 影响冷启动基准                                           |
| **Execution timeout**    | **360 s（6 分钟）**                                   | 与 `policy.executionTimeout` 对齐                    |


---



## API 响应结构

### 成功

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
| `timings`    | 各阶段耗时（可观测性）                                               |


RunPod 任务状态对象另提供 `delayTime`、`executionTime`（毫秒），用于 SLA 看板；`timings` 为容器内分解。

### 失败


| 响应形态                                                                                                                  | 场景               |
| --------------------------------------------------------------------------------------------------------------------- | ---------------- |
| `{"error": "source_image_base64 is required"}`                                                                        | 参数校验失败           |
| `{"error": "headless-run failed (exit N)", "stderr": "...", "stdout": "...", "diagnostics": {...}, "timings": {...}}` | FaceFusion 子进程失败 |
| `{"error": "...", "traceback": "..."}`                                                                                | 未捕获异常            |


**快速失败（`executionTime` 约 2 秒）** 通常表示配置错误（缺少 R2 环境变量、`extra_args` 非法、`target_url` 无效等），**而非**执行超时。请先查看 `stderr` 与 `error`，再调整超时。

### 请求级 `policy`（RunPod 平台）

与 `input` 同级，可选；**不由 `handler.py` 读取**，由 RunPod 强制执行：


| 字段                 | 示例                | 含义                        |
| ------------------ | ----------------- | ------------------------- |
| `executionTimeout` | `360000`（毫秒，6 分钟） | RunPod 允许 handler 运行的最长时间 |
| `ttl`              | `3600000`         | 任务在队列中的存活时间（TTL）          |
| `lowPriority`      | `false`           | 低优先级队列提示                  |


请在端点控制台与每次请求的 `policy` 中将 `executionTimeout` 设为 **360000 ms（6 分钟）**。

### `input` 完整字段（RunPod 包装为 `event.input`）


| 字段                    | 必填    | 默认值                | 说明                             |
| --------------------- | ----- | ------------------ | ------------------------------ |
| `source_image_base64` | **是** | —                  | Base64 编码的源人脸（PNG/JPEG）        |
| `target_url`          | **是** | —                  | 待处理目标视频（或图片）的 HTTPS URL        |
| `source_image_format` | 否     | `png`              | 解码后源文件扩展名                      |
| `output_format`       | 否     | `mp4`              | 输出容器/扩展名                       |
| `processors`          | 否     | `["face_swapper"]` | FaceFusion `--processors` 列表   |
| `face_swapper_model`  | 否     | FaceFusion 默认      | 如 `inswapper_128_fp16`（镜像内已烘焙） |
| `extra_args`          | 否     | `[]`               | 追加到 `headless-run` 的 CLI 参数    |


参考载荷：[test_input.json](test_input.json)、[tests/sample_input.json](tests/sample_input.json)。

---



## R2 URL 与存储

Handler 下载目标素材：

- **R2 / S3 兼容 URL** — boto3 + 端点凭证（`*.r2.cloudflarestorage.com` 或 `R2_PUBLIC_BASE_URL` 下 URL）。
- **其他 HTTPS URL** — `requests` 流式下载（公开或签名 GET）。

支持的路径风格（`handler.py` → `_parse_r2_url`）：

- 虚拟主机：`https://<bucket>.<account>.r2.cloudflarestorage.com/path/to/object.mp4`
- 路径风格：`https://<account>.r2.cloudflarestorage.com/<bucket>/path/to/object.mp4`
- 公共域名：`https://your-cdn.example.com/...`（需 `R2_PUBLIC_BASE_URL` + `R2_BUCKET`）

**输出桶：** `target_url` 能解析出桶则输出到该桶，否则 `R2_BUCKET`。对象键恒为 `outputs/{uuid}.{output_format}`。

---



## 模型性能调试参数

FaceFusion CLI 参数经 `extra_args` 原样透传。应用可将画质/延迟预设映射为参数组。

基准配置档见 [tests/benchmark/config.example.json](tests/benchmark/config.example.json)：


| 配置档（Profile）          | 定位                                                |
| --------------------- | ------------------------------------------------- |
| `baseline_e2e`        | 画质优先：`libx264`、`veryslow`、512×512 pixel boost     |
| `fast_nvenc`          | 延迟目标：CUDA、NVENC、256×256 boost、4 线程                |
| `fast_nvenc_threads8` | 同上，8 执行线程                                         |
| `sla_45s`             | 激进 SLA：128 boost、0.5 缩放、15 fps 上限、NVENC ultrafast |


`fast_nvenc` 示例（写入 `input.extra_args`）：

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

完整参数：`python facefusion.py headless-run --help` 或 [FaceFusion 文档](https://docs.facefusion.io)。

---

## 环境变量参考

### Worker 容器（RunPod 端点 / Docker）


| 变量                     | 必填  | 说明                        |
| ---------------------- | --- | ------------------------- |
| `R2_ACCOUNT_ID`        | 是*  | Cloudflare 账户 ID，默认 R2 端点 |
| `R2_ACCESS_KEY_ID`     | 是   | S3 Access Key             |
| `R2_SECRET_ACCESS_KEY` | 是   | S3 Secret Key             |
| `R2_BUCKET`            | 是   | 默认输出桶                     |
| `R2_PUBLIC_BASE_URL`   | 否   | 公共输出 URL 根路径及 R2 URL 解析   |
| `R2_ENDPOINT`          | 否   | 自定义 S3 端点（MinIO 等）        |


已完整指定 `R2_ENDPOINT` 时 `R2_ACCOUNT_ID` 可为占位（如 MinIO 用 `local`）。

**安全：** 切勿在客户端请求中嵌入 R2 密钥。

### Docker 镜像构建（`FF_`*，可选）

仅用于 `Dockerfile` 中 `tools/preload_face_swap_models.py`：


| 变量                            | 默认值                  | 说明             |
| ----------------------------- | -------------------- | -------------- |
| `FF_FACE_SWAPPER_MODEL`       | `inswapper_128_fp16` | 预烘焙换脸 ONNX     |
| `FF_FACE_DETECTOR_MODEL`      | `yolo_face`          | 检测模型           |
| `FF_FACE_LANDMARKER_MODEL`    | `2dfan4`             | 关键点模型          |
| `FF_FACE_OCCLUDER_MODEL`      | `xseg_1`             | 遮挡模型           |
| `FF_FACE_PARSER_MODEL`        | `bisenet_resnet_34`  | 解析模型           |
| `FF_VOICE_EXTRACTOR_MODEL`    | `kim_vocal_2`        | 人声模型           |
| `FF_DOWNLOAD_SCOPE`           | `lite`               | 预下载范围          |
| `FF_PRELOAD_CONTENT_ANALYSER` | `0`                  | `1` = 预加载内容分析  |
| `FF_PRELOAD_FACE_CLASSIFIER`  | `0`                  | `1` = 预加载人脸分类器 |
| `FF_PRELOAD_VOICE_EXTRACTOR`  | `0`                  | `1` = 预加载人声提取器 |


### 后端 / CI（RunPod API、基准测试）


| 变量                          | 必填       | 说明               |
| --------------------------- | -------- | ---------------- |
| `RUNPOD_API_KEY`            | 调用 API 时 | Bearer 令牌        |
| `ENDPOINT_ID`               | 调用 API 时 | Serverless 端点 ID |
| `BENCHMARK_TARGET_60S_URL`  | 仅基准      | 60s 测试视频 R2 URL  |
| `BENCHMARK_TARGET_120S_URL` | 仅基准      | 120s 测试视频 R2 URL |
| `BENCHMARK_TARGET_300S_URL` | 仅基准      | 300s 测试视频 R2 URL |


### 调试（可选）


| 变量                     | 说明                   |
| ---------------------- | -------------------- |
| `AGENT_DEBUG_LOG_PATH` | Agent 调试 NDJSON 额外路径 |


容器启动时，`handler.py` 打印 Python/依赖版本、ONNX Runtime providers，以及哪些 `R2_*` 已设置（不打印值）。

---



## 镜像构建

在仓库根目录 `docker build` 时：

- 基础镜像：`nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04`，Python 3.11，FFmpeg
- 依赖：`requirements.txt` + `runpod_serverless/requirements-runpod.txt`，`onnxruntime-gpu==1.24.4`
- 预加载：`tools/preload_face_swap_models.py`，并校验 `.assets/models/` 下核心 ONNX（如 `yoloface_8n.onnx`、`inswapper_128_fp16.onnx` 等）
- 启动命令：`python3 -u runpod_serverless/handler.py`

模型在构建阶段烘焙，避免冷启动首次请求再下载权重。

---

## 超时配置清单


| 层级                           | 建议值                                            |
| ---------------------------- | ---------------------------------------------- |
| RunPod 端点执行超时                | **360 s（6 分钟）**                                |
| 请求 `policy.executionTimeout` | **360000 ms（6 分钟）**                            |
| 客户端状态轮询超时                    | **420 s（7 分钟）** 或更长                            |
| `target_url` HTTP 下载         | Handler 内 socket 超时 **300 s**                  |
| 基准客户端 `job_timeout_seconds`  | 默认 **1800 s**（见 `tests/benchmark/config.json`） |


---

## 运维说明

- **并发：** 通常每 GPU 同时一个重负载换脸；横向扩容靠增加 Worker，而非单卡多任务。
- **幂等性：** 每次任务写入新键 `outputs/{uuid}.`*；业务侧应关联 RunPod `job_id` 与用户请求。
- **源图大小：** 过大 Base64 增加请求体与 `decode_source_ms`；建议在服务端限制分辨率。
- **模型变更：** 修改 `face_swapper_model` 或 `FF_`* 预加载变量后须 **重新构建镜像**。
- **上游 FaceFusion：** CLI 行为以 [FaceFusion 文档](https://docs.facefusion.io) 为准；本 Fork 仅增加 RunPod Handler 与 R2 I/O。

### 本地与线上验证


| 场景         | 命令 / 文档                                                                        |
| ---------- | ------------------------------------------------------------------------------ |
| Handler 冒烟 | `python runpod_serverless/handler.py` + [test_input.json](test_input.json)     |
| GPU e2e    | `bash runpod_serverless/tests/run_e2e.sh` → [tests/README.md](tests/README.md) |
| 线上基准       | [tests/benchmark/README.md](tests/benchmark/README.md)                         |


---

## 相关文件


| 路径                                                 | 用途                  |
| -------------------------------------------------- | ------------------- |
| [handler.py](handler.py)                           | Serverless Handler  |
| [requirements-runpod.txt](requirements-runpod.txt) | Worker 额外 Python 依赖 |
| [test_input.json](test_input.json)                 | 本地冒烟输入              |
| [../Dockerfile](../Dockerfile)                     | 生产镜像（仓库根目录）         |
| [tests/](tests/)                                   | e2e 与 benchmark     |


