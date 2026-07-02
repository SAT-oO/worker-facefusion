from typing import Tuple

import numpy

from facefusion.types import VisionFrame

SWAP_CROP_MIN_MEAN = 15.0
SWAP_CROP_BLACK_PIXEL_THRESHOLD = 10
SWAP_CROP_MAX_BLACK_RATIO = 0.5


def is_degenerate_swap_crop(crop_vision_frame : VisionFrame) -> Tuple[bool, dict]:
	if crop_vision_frame is None or crop_vision_frame.size == 0:
		return True, {
			'mean_brightness': 0.0,
			'black_pixel_ratio': 1.0
		}

	crop = crop_vision_frame.astype(numpy.float32)
	mean_brightness = float(crop.mean())
	black_pixels = numpy.all(crop < SWAP_CROP_BLACK_PIXEL_THRESHOLD, axis = 2)
	black_pixel_ratio = float(black_pixels.mean())
	metrics = {
		'mean_brightness': round(mean_brightness, 2),
		'black_pixel_ratio': round(black_pixel_ratio, 4)
	}
	is_degenerate = mean_brightness < SWAP_CROP_MIN_MEAN or black_pixel_ratio > SWAP_CROP_MAX_BLACK_RATIO
	return is_degenerate, metrics
