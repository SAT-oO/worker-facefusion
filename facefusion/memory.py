from typing import List

from facefusion.common_helper import is_macos, is_windows

if is_windows():
	import ctypes
else:
	import resource

_NVENC_VIDEO_ENCODERS = frozenset({ 'h264_nvenc', 'hevc_nvenc' })


def release_gpu_vram_for_video_encode(video_encoder : str) -> None:
	"""Drop ONNX CUDA sessions before NVENC merge so ffmpeg can allocate encode buffers.

	With video_memory_strategy tolerant, inference pools stay loaded through frame
	processing; NVENC then competes for the same VRAM and can fail with OOM-sized
	allocations on containerized GPUs.
	"""
	if video_encoder not in _NVENC_VIDEO_ENCODERS:
		return

	from facefusion import content_analyser, face_classifier, face_detector, face_landmarker, face_masker, face_recognizer, state_manager
	from facefusion.processors.core import get_processors_modules

	processors = state_manager.get_item('processors') or []
	for processor_module in get_processors_modules(processors):
		clear_inference_pool = getattr(processor_module, 'clear_inference_pool', None)
		if clear_inference_pool:
			clear_inference_pool()

	for module in ( content_analyser, face_classifier, face_detector, face_landmarker, face_masker, face_recognizer ):
		module.clear_inference_pool()


def limit_system_memory(system_memory_limit : int = 1) -> bool:
	if is_macos():
		system_memory_limit = system_memory_limit * (1024 ** 6)
	else:
		system_memory_limit = system_memory_limit * (1024 ** 3)
	try:
		if is_windows():
			ctypes.windll.kernel32.SetProcessWorkingSetSize(-1, ctypes.c_size_t(system_memory_limit), ctypes.c_size_t(system_memory_limit)) #type:ignore[attr-defined]
		else:
			resource.setrlimit(resource.RLIMIT_DATA, (system_memory_limit, system_memory_limit))
		return True
	except Exception:
		return False
