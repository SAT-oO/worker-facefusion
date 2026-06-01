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

RUN python facefusion.py force-download 

FROM base AS final
WORKDIR /app

COPY . .
COPY --from=models /app/.assets /app/.assets

CMD ["python3", "-u", "handler.py"]
