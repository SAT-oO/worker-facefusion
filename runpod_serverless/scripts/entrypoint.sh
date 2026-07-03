#!/bin/sh
# Worker container entrypoint. GPU topology for NVENC is handled by the ffmpeg
# nvscope wrapper; no /dev/nvidia0 symlink remapping is required.
set -eu

export NVSCOPE_LIB="${NVSCOPE_LIB:-/usr/local/lib/nvscope/libnvscope.so}"

if [ "${NVSCOPE_DISABLED:-0}" != "1" ] && command -v nvidia-smi >/dev/null 2>&1; then
	if [ -z "${NVSCOPE_GPU_UUID:-}" ]; then
		gpu_uuid="$(nvidia-smi --query-gpu=uuid --format=csv,noheader 2>/dev/null | head -1 | tr -d ' ')"
		if [ -n "$gpu_uuid" ]; then
			export NVSCOPE_GPU_UUID="$gpu_uuid"
		fi
	fi
fi

if [ "${NVSCOPE_DISABLED:-0}" != "1" ] && [ -x /usr/local/bin/nvscope-probe ]; then
	/usr/local/bin/nvscope-probe >/dev/null 2>&1 || true
fi

cd /app
exec python3 -u runpod_serverless/handler.py "$@"
