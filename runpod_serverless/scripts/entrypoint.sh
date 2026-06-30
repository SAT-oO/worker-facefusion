#!/bin/sh
# Worker container entrypoint. GPU topology for NVENC is handled by the ffmpeg
# nvscope wrapper; no /dev/nvidia0 symlink remapping is required.
set -eu

cd /app
exec python3 -u runpod_serverless/handler.py "$@"
