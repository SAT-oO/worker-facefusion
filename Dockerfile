FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04 AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common \
        ca-certificates \
        curl \
        git \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
        python3.11 \
        python3.11-venv \
        python3.11-distutils \
    && curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11 \
    && ln -sf /usr/bin/python3.11 /usr/local/bin/python \
    && ln -sf /usr/bin/python3.11 /usr/local/bin/python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-runpod.txt ./

RUN python -m pip install --upgrade pip \
    && pip install -r requirements.txt \
    && pip install -r requirements-runpod.txt \
    && pip uninstall -y onnxruntime onnxruntime-gpu \
    && pip install onnxruntime-gpu==1.24.4

# #region build-time smoke test
RUN python - <<'PY'
import sys
mods = ["cv2", "numpy", "onnx", "onnxruntime", "scipy", "tqdm", "boto3", "requests", "runpod"]
for m in mods:
    mod = __import__(m)
    print(f"  ok  {m}=={getattr(mod, '__version__', '?')}")
print("python", sys.version)
import onnxruntime as ort
print("onnxruntime providers:", ort.get_available_providers())
PY
# #endregion

# Stage dedicated to model baking so normal app code changes
# do not invalidate expensive model downloads.
FROM base AS models
WORKDIR /app

COPY facefusion/ ./facefusion/
COPY facefusion.py ./
COPY tools/preload_face_swap_models.py ./tools/preload_face_swap_models.py

RUN set -eux; \
    for attempt in 1 2 3; do \
      rm -rf /app/.assets/models && mkdir -p /app/.assets/models; \
      if python tools/preload_face_swap_models.py; then \
        break; \
      fi; \
      if [ "$attempt" -eq 3 ]; then \
        exit 1; \
      fi; \
      echo "targeted preload failed, retrying..." ; \
    done

RUN python - <<'PY'
import os, sys
models = {
    'yoloface_8n.onnx':          5_000_000,
    '2dfan4.onnx':               80_000_000,
    'arcface_w600k_r50.onnx':    160_000_000,
    'inswapper_128_fp16.onnx':   250_000_000,
    'bisenet_resnet_34.onnx':    40_000_000,
    'xseg_1.onnx':               500_000,
}
failed = False
for name, min_size in models.items():
    path = f'/app/.assets/models/{name}'
    size = os.path.getsize(path) if os.path.exists(path) else 0
    if size < min_size:
        print(f'FAIL: {name} is {size} bytes (expected >= {min_size})', file=sys.stderr)
        failed = True
    else:
        print(f'  ok  {name} = {size / 1024 / 1024:.1f} MB')
if failed:
    sys.exit(1)
PY

FROM base AS final
WORKDIR /app

COPY . .
COPY --from=models /app/.assets /app/.assets

CMD ["python3", "-u", "handler.py"]
