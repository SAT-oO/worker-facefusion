import os
import sys
import json
import time
from pathlib import Path

DEBUG_LOG_PATH = "/Users/sat-oo/worker-facefusion/.cursor/debug-f92872.log"


def _debug_log(run_id: str, hypothesis_id: str, location: str, message: str, data: dict) -> None:
	# #region agent log
	try:
		payload = {
			"sessionId": "f92872",
			"runId": run_id,
			"hypothesisId": hypothesis_id,
			"location": location,
			"message": message,
			"data": data,
			"timestamp": int(time.time() * 1000),
		}
		with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as debug_file:
			debug_file.write(json.dumps(payload) + "\n")
	except Exception:
		pass
	# #endregion


def _env(name: str, default: str) -> str:
	value = os.environ.get(name)
	return value if value else default


def main() -> int:
	# #region agent log
	project_root = str(Path(__file__).resolve().parents[1])
	if project_root not in sys.path:
		sys.path.insert(0, project_root)
	_debug_log(
		"pre-fix",
		"H5",
		"tools/preload_face_swap_models.py:41",
		"ensured project root on sys.path",
		{
			"project_root": project_root,
			"sys_path_head": sys.path[:5],
		},
	)
	# #endregion

	# #region agent log
	_debug_log(
		"pre-fix",
		"H1",
		"tools/preload_face_swap_models.py:55",
		"bootstrap python path state",
		{
			"cwd": os.getcwd(),
			"sys_path_head": sys.path[:5],
			"has_repo_facefusion": os.path.isdir(os.path.join(os.getcwd(), "facefusion")),
			"has_app_facefusion": os.path.isdir("/app/facefusion"),
		},
	)
	# #endregion
	try:
		from facefusion import content_analyser, face_classifier, face_detector, face_landmarker, face_masker, face_recognizer, logger, state_manager, voice_extractor
		from facefusion.processors.modules.face_swapper import core as face_swapper
	except Exception as exc:
		# #region agent log
		_debug_log(
			"pre-fix",
			"H2",
			"tools/preload_face_swap_models.py:73",
			"facefusion import failed",
			{
				"error_type": type(exc).__name__,
				"error": str(exc),
				"cwd": os.getcwd(),
				"sys_path_head": sys.path[:5],
			},
		)
		# #endregion
		raise

	logger.init("info")

	# Pin exact model families to avoid downloading optional variants.
	state_manager.init_item("face_detector_model", _env("FF_FACE_DETECTOR_MODEL", "yolo_face"))
	state_manager.init_item("face_landmarker_model", _env("FF_FACE_LANDMARKER_MODEL", "2dfan4"))
	state_manager.init_item("face_occluder_model", _env("FF_FACE_OCCLUDER_MODEL", "xseg_1"))
	state_manager.init_item("face_parser_model", _env("FF_FACE_PARSER_MODEL", "bisenet_resnet_34"))
	state_manager.init_item("voice_extractor_model", _env("FF_VOICE_EXTRACTOR_MODEL", "kim_vocal_2"))
	state_manager.init_item("face_swapper_model", _env("FF_FACE_SWAPPER_MODEL", "inswapper_128_fp16"))
	state_manager.init_item("download_scope", _env("FF_DOWNLOAD_SCOPE", "lite"))
	state_manager.init_item("download_providers", ["github", "huggingface"])
	# #region agent log
	_debug_log(
		"pre-fix",
		"H1",
		"tools/preload_face_swap_models.py:92",
		"initialized preload state",
		{
			"face_detector_model": state_manager.get_item("face_detector_model"),
			"face_landmarker_model": state_manager.get_item("face_landmarker_model"),
			"face_swapper_model": state_manager.get_item("face_swapper_model"),
			"download_scope": state_manager.get_item("download_scope"),
			"download_providers": state_manager.get_item("download_providers"),
		},
	)
	# #endregion

	modules = [
		content_analyser,
		face_classifier,
		face_detector,
		face_landmarker,
		face_masker,
		face_recognizer,
		voice_extractor,
		face_swapper,
	]

	for module in modules:
		# #region agent log
		_debug_log(
			"pre-fix",
			"H2",
			"tools/preload_face_swap_models.py:118",
			"running module pre_check",
			{"module": module.__name__},
		)
		# #endregion
		if not module.pre_check():
			# #region agent log
			_debug_log(
				"pre-fix",
				"H3",
				"tools/preload_face_swap_models.py:126",
				"module pre_check failed",
				{"module": module.__name__},
			)
			# #endregion
			logger.error(f"preload failed in {module.__name__}", __name__)
			return 1

	# #region agent log
	_debug_log(
		"pre-fix",
		"H4",
		"tools/preload_face_swap_models.py:139",
		"all module pre_check calls succeeded",
		{},
	)
	# #endregion
	logger.info("targeted preload complete", __name__)
	return 0


if __name__ == "__main__":
	sys.exit(main())
