RunPod 基准测试
===============

面向 FaceFusion Serverless Worker 的基准测试脚本：向线上 RunPod 端点提交任务、轮询直至完成，并将延迟数据写入 `results/`。

> 简体中文。English: [README_EN.md](README_EN.md)。

前置条件
--------

- 已部署 RunPod Serverless 端点（镜像 + `R2_*` 环境变量）。部署见 [SETUP_INSTRUCTIONS_CN.md](../../SETUP_INSTRUCTIONS_CN.md)；超时与配置档见 [MAINTENANCE_SPECIFICATION_CN.md](../../MAINTENANCE_SPECIFICATION_CN.md)。
- `RUNPOD_API_KEY` 与端点 ID（`ENDPOINT_ID`）。
- 目标视频已放在 R2（或 Worker 可下载的任意 URL）——通常使用 60s / 120s / 300s 片段以覆盖 SLA 区间。

一次性配置
----------

```bash
# 每个任务共用的源人脸固件（Base64）
bash runpod_serverless/tests/fetch_fixtures.sh
```

编辑 [`config.json`](config.json)（通常只需设置端点）：

| 字段 | 填写说明 |
| --- | --- |
| `endpoint_id` | RunPod Serverless 端点 ID，或写 `${ENDPOINT_ID}` 并 `export ENDPOINT_ID` |
| `fixtures.default_target_key` | 省略 `--target` 时的默认键（如 `120s`） |

`targets` 默认引用 `${BENCHMARK_TARGET_60S_URL}` 等环境变量；未设置时，脚本会使用 `benchmark_common.py` 中的公共 R2 固件 URL。也可用 `--target-url` 单次覆盖。

```bash
export RUNPOD_API_KEY=...
export ENDPOINT_ID=...   # 若 config 中使用 ${ENDPOINT_ID}
# 可选：覆盖默认固件
export BENCHMARK_TARGET_120S_URL="https://your-bucket.../target_120s.mp4"
```

运行
----

在仓库根目录执行：

```bash
# 冷启动：任务间留缩容间隔（默认任务间隔空闲 90s，共 10 次）
python3 runpod_serverless/tests/benchmark/run_benchmark.py \
  --scenario cold_flashboot \
  --profile fast_nvenc \
  --target 120s

# 热机：连续提交（任务间不等待）
python3 runpod_serverless/tests/benchmark/run_benchmark.py \
  --scenario warm \
  --profile fast_nvenc \
  --target 120s

# 并发突发（并发数由 config 决定，常为 4）
python3 runpod_serverless/tests/benchmark/run_benchmark.py \
  --scenario concurrent \
  --profile fast_nvenc \
  --target 120s

# 同一测试素材下对比不同配置档
python3 runpod_serverless/tests/benchmark/run_benchmark.py --scenario warm --profile baseline_e2e --target 120s
python3 runpod_serverless/tests/benchmark/run_benchmark.py --scenario warm --profile fast_nvenc --target 120s
```

常用参数覆盖：

```bash
python3 runpod_serverless/tests/benchmark/run_benchmark.py \
  --scenario warm --profile fast_nvenc --target 120s \
  --target-url "https://..." \
  --iterations 5 \
  --wait-after-job 0 \
  --concurrency 2
```

| 参数 | 含义 |
| --- | --- |
| `--config` | 配置文件路径（默认本目录 `config.json`） |
| `--scenario` | 场景：`cold_flashboot`、`warm`、`concurrent` |
| `--profile` | `config.profiles` 中的名称（如 `fast_nvenc`） |
| `--target` | `config.targets` 中的键（如 `120s`） |
| `--target-url` | 覆盖目标视频 URL（不使用 `config.targets`） |
| `--iterations` | 覆盖场景中的运行次数 |
| `--concurrency` | 每批并行任务数 |
| `--wait-after-job` | 批次之间的休眠秒数（冷启动场景） |
| `--output` | 自定义 JSONL 输出路径 |

场景（`config.json` → `scenarios`）
-----------------------------------

| 场景 | 默认行为 |
| --- | --- |
| `cold_flashboot` | 10 次任务，任务间隔等待 90s，并发 1 |
| `warm` | 20 次任务，无间隔，并发 1 |
| `concurrent` | 1 批，并发 4 |

配置档（`config.json` → `profiles`）
----------------------------------

| 配置档 | 说明 |
| --- | --- |
| `baseline_e2e` | 画质优先：`libx264`、`veryslow`、512×512 pixel boost |
| `fast_nvenc` | 延迟优先：CUDA、NVENC、256×256 boost、4 线程 |
| `fast_nvenc_threads8` | 同 `fast_nvenc`，8 执行线程 |
| `production` | 生产基线：输出与源片同分辨率/帧率（quality_tiers tier 1） |

结果
----

每次运行生成：

- `results/<timestamp>_<scenario>_<profile>.jsonl` — 每个任务一行 JSON
- `results/<timestamp>_<scenario>_<profile>.summary.json` — `delayTime`、`executionTime` 及客户端 `total_time_ms` 的 p50/p90/p99

汇总多次运行：

```bash
python3 runpod_serverless/tests/benchmark/analyze_results.py runpod_serverless/tests/benchmark/results/*.jsonl
```

每条任务记录字段：

| 字段 | 来源 |
| --- | --- |
| `delay_time_ms` | RunPod 任务状态（`delayTime`，排队 + 冷启动） |
| `execution_time_ms` | RunPod 任务状态（`executionTime`，Handler 执行时间） |
| `total_time_ms` | 客户端从提交到完成的墙钟时间 |
| `handler_timings` | Handler 返回的 `timings`（解码、下载、facefusion、上传） |
| `output_url` | 成功时的输出 URL |

超时
----

| 配置项 | 位置 | 默认值 |
| --- | --- | --- |
| `policy.executionTimeout` | `config.json` → `base_input.policy` | 360000 ms（6 分钟） |
| `job_timeout_seconds` | `config.json` → `runpod` | 1800 s（客户端轮询上限） |
| 端点执行超时 | RunPod 控制台 | **360 s**（6 分钟） |

若 `execution_time_ms` 只有数秒，通常是任务提前失败（`extra_args` 错误、缺少 R2 环境变量、`target_url` 无效等），**不是**超时。请查看 JSONL 中的 `error` 与 `stderr_tail`。

建议运行顺序
------------

1. `warm` + `120s` — 对比 `baseline_e2e` 与 `fast_nvenc`（纯算力/编码）。
2. `cold_flashboot` + `120s` + `fast_nvenc` — 冷启动与任务间缩容间隔。
3. `warm` + `300s` — 较长片段稳定性。
4. `concurrent` — 队列深度与多任务并发行为。
