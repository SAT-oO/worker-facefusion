import numpy

from facefusion.source_validator import (
	embedding_cosine_similarity,
	eye_aspect_ratio,
	format_validation_error,
	landmark_asymmetry,
)
from facefusion.swap_quality import is_degenerate_swap_crop


def test_is_degenerate_swap_crop_detects_black_frame() -> None:
	crop = numpy.zeros((128, 128, 3), dtype = numpy.uint8)
	is_degenerate, metrics = is_degenerate_swap_crop(crop)

	assert is_degenerate is True
	assert metrics['mean_brightness'] == 0.0
	assert metrics['black_pixel_ratio'] == 1.0


def test_is_degenerate_swap_crop_accepts_normal_frame() -> None:
	crop = numpy.full((128, 128, 3), 128, dtype = numpy.uint8)
	is_degenerate, metrics = is_degenerate_swap_crop(crop)

	assert is_degenerate is False
	assert metrics['mean_brightness'] == 128.0
	assert metrics['black_pixel_ratio'] == 0.0


def test_eye_aspect_ratio_open_eyes() -> None:
	landmarks = numpy.zeros((68, 2), dtype = numpy.float32)
	landmarks[36] = (10, 10)
	landmarks[37] = (12, 9)
	landmarks[38] = (14, 9)
	landmarks[39] = (16, 10)
	landmarks[40] = (14, 11)
	landmarks[41] = (12, 11)

	ear = eye_aspect_ratio(landmarks, (36, 37, 38, 39, 40, 41))

	assert ear > 0.2


def test_eye_aspect_ratio_closed_eyes() -> None:
	landmarks = numpy.zeros((68, 2), dtype = numpy.float32)
	landmarks[36] = (10, 10)
	landmarks[37] = (12, 10)
	landmarks[38] = (14, 10)
	landmarks[39] = (16, 10)
	landmarks[40] = (14, 10.2)
	landmarks[41] = (12, 10.2)

	ear = eye_aspect_ratio(landmarks, (36, 37, 38, 39, 40, 41))

	assert ear < 0.15


def test_landmark_asymmetry_balanced() -> None:
	landmarks = numpy.zeros((68, 2), dtype = numpy.float32)
	landmarks[30] = (20, 20)
	landmarks[36:42, 1] = 10
	landmarks[42:48, 1] = 10
	landmarks[36, 0] = 10
	landmarks[45, 0] = 30

	assert landmark_asymmetry(landmarks) == 0.0


def test_embedding_cosine_similarity_identical() -> None:
	embedding = numpy.array([ 1.0, 0.0, 0.0 ], dtype = numpy.float64)

	assert embedding_cosine_similarity(embedding, embedding) == 1.0


def test_format_validation_error() -> None:
	message = format_validation_error({ 'reasons': [ 'landmarker_below_threshold', 'face_too_small' ] })

	assert message.startswith('source_face_quality_insufficient:')
	assert 'landmarker_below_threshold' in message
	assert 'face_too_small' in message
