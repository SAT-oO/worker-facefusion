FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.10 \
        python3.10-venv \
        python3-pip \
        ffmpeg \
        curl \
        git \
        ca-certificates \
    && ln -sf /usr/bin/python3.10 /usr/bin/python \
    && ln -sf /usr/bin/python3.10 /usr/bin/python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-runpod.txt ./

RUN python -m pip install --upgrade pip \
    && pip install -r requirements.txt \
    && pip install -r requirements-runpod.txt \
    && pip uninstall -y onnxruntime onnxruntime-gpu || true \
    && pip install onnxruntime-gpu==1.23.2

COPY . .

RUN python facefusion.py force-download || true

CMD ["python3", "-u", "handler.py"]
