# RunPod Serverless — 使用说明

> GitHub 浏览本目录时默认显示本文档。部署与集成细节见 **[SETUP_INSTRUCTIONS_CN.md](SETUP_INSTRUCTIONS_CN.md)**；技术规格与运维见 **[MAINTENANCE_SPECIFICATION_CN.md](MAINTENANCE_SPECIFICATION_CN.md)**。English: [RUNPOD_SERVERLESS.md](RUNPOD_SERVERLESS.md)。

向已部署的 RunPod 端点提交换脸任务，轮询完成后从返回的 `output_url` 下载结果视频。无需了解仓库内部实现。

---

## 前置条件

由运维/平台侧完成（步骤见 [SETUP_INSTRUCTIONS_CN.md](SETUP_INSTRUCTIONS_CN.md)）：

- RunPod Serverless 端点已指向 `worker-facefusion` 镜像，并配置好 R2 环境变量
- 你持有 **`RUNPOD_API_KEY`** 与 **`ENDPOINT_ID`**

每次任务你需要：

1. **源人脸** — 一张人脸图片，转为 Base64 字符串  
2. **目标素材** — 已上传到 R2（或任意 Worker 可访问的 HTTPS URL）的视频/图片链接，作为 `target_url`

---

## 提交任务

```bash
export RUNPOD_API_KEY=...
export ENDPOINT_ID=...
```

**方式 A — curl**

```bash
curl -sS -X POST "https://api.runpod.ai/v2/${ENDPOINT_ID}/run" \
  -H "Authorization: Bearer ${RUNPOD_API_KEY}" \
  -H "Content-Type: application/json" \
  -d @body.json
```

**方式 B — Python**（[`submit_job.py`](submit_job.py)）

```bash
python runpod_serverless/submit_job.py --body body.json --poll
```

`body.json` 示例：

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

提交成功会返回 `id`（任务 ID）。

---

## 查询结果

```bash
curl -sS "https://api.runpod.ai/v2/${ENDPOINT_ID}/status/<job_id>" \
  -H "Authorization: Bearer ${RUNPOD_API_KEY}"
```

或使用 `submit_job.py --poll`（提交时加 `--poll` 会自动轮询）。

| `status` | 含义 |
| --- | --- |
| `IN_QUEUE` / `IN_PROGRESS` | 继续轮询（建议每 2–5 秒） |
| `COMPLETED` | 成功，读取 `output.output_url` |
| `FAILED` / `TIMED_OUT` / `CANCELLED` | 失败，查看 `output.error` 或 `error` |

成功时关注字段：

- `output.output_url` — 结果视频地址  
- `output.output_key` — 对象键（便于后续管理）

---

## 文档索引

| 文档 | 何时阅读 |
| --- | --- |
| [SETUP_INSTRUCTIONS_CN.md](SETUP_INSTRUCTIONS_CN.md) | 构建镜像、配置 RunPod 端点、完整集成流程、本地测试 |
| [MAINTENANCE_SPECIFICATION_CN.md](MAINTENANCE_SPECIFICATION_CN.md) | API 响应结构、R2 URL 规则、性能配置档、超时与运维 |
| [submit_job.py](submit_job.py) | 脚本参数（`--source-image`、`--target-url` 等） |
| [RUNPOD_SERVERLESS.md](RUNPOD_SERVERLESS.md) | English |
