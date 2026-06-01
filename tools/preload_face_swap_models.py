import os
import sys

from facefusion import content_analyser, face_classifier, face_detector, face_landmarker, face_masker, face_recognizer, logger, state_manager, voice_extractor
from facefusion.processors.modules.face_swapper import core as face_swapper


def _env(name: str, default: str) -> str:
	value = os.environ.get(name)
	return value if value else default


def main() -> int:
	logger.init("info")

	# Pin exact model families to avoid downloading optional variants.
	state_manager.init_item("face_detector_model", _env("FF_FACE_DETECTOR_MODEL", "yolo_face"))
	state_manager.init_item("face_landmarker_model", _env("FF_FACE_LANDMARKER_MODEL", "2dfan4"))
	state_manager.init_item("face_occluder_model", _env("FF_FACE_OCCLUDER_MODEL", "xseg_1"))
	state_manager.init_item("face_parser_model", _env("FF_FACE_PARSER_MODEL", "bisenet_resnet_34"))
	state_manager.init_item("voice_extractor_model", _env("FF_VOICE_EXTRACTOR_MODEL", "kim_vocal_2"))
	state_manager.init_item("face_swapper_model", _env("FF_FACE_SWAPPER_MODEL", "inswapper_128_fp16"))
	state_manager.init_item("download_scope", _env("FF_DOWNLOAD_SCOPE", "lite"))

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
		if not module.pre_check():
			logger.error(f"preload failed in {module.__name__}", __name__)
			return 1

	logger.info("targeted preload complete", __name__)
	return 0


if __name__ == "__main__":
	sys.exit(main())
