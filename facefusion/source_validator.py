import json
from typing import List, Tuple

import cv2
import numpy

from facefusion.face_analyser import get_many_faces
from facefusion.face_helper import warp_face_by_face_landmark_5
from facefusion.face_masker import create_occlusion_mask, create_region_mask
from facefusion.face_recognizer import calculate_face_embedding
from facefusion.face_selector import sort_faces_by_order
from facefusion import state_manager
from facefusion.types import Face, VisionFrame

MIN_DETECTOR_SCORE = 0.7
MIN_LANDMARKER_SCORE = 0.55
MIN_FACE_SHORT_SIDE_RATIO = 0.15
MIN_EYE_ASPECT_RATIO = 0.18
MIN_EMBEDDING_COSINE_SIM = 0.85
MIN_ALIGNED_SHARPNESS = 50.0
MIN_OCCLUSION_CENTER_MEAN = 0.2
MIN_EYE_REGION_PEAK = 0.15
MIN_MOUTH_REGION_PEAK = 0.25
MAX_LANDMARK_ASYMMETRY = 0.35

SOURCE_QUALITY_MARKER = 'source_face_quality_insufficient'

LEFT_EYE_INDICES = (36, 37, 38, 39, 40, 41)
RIGHT_EYE_INDICES = (42, 43, 44, 45, 46, 47)


def is_source_validation_enabled() -> bool:
	return not state_manager.get_item('skip_source_validation')


def select_source_samples(source_vision_frames : List[VisionFrame]) -> List[Tuple[VisionFrame, Face]]:
	source_samples : List[Tuple[VisionFrame, Face]] = []

	for vision_frame in source_vision_frames:
		if vision_frame is None or not numpy.any(vision_frame):
			continue
		faces = sort_faces_by_order(get_many_faces([ vision_frame ]), 'large-small')
		if faces:
			source_samples.append((vision_frame, faces[0]))
	return source_samples


def eye_aspect_ratio(face_landmark_68 : numpy.ndarray, eye_indices : Tuple[int, ...]) -> float:
	points = face_landmark_68[list(eye_indices)]
	vertical_1 = numpy.linalg.norm(points[1] - points[5])
	vertical_2 = numpy.linalg.norm(points[2] - points[4])
	horizontal = numpy.linalg.norm(points[0] - points[3])
	if horizontal <= 0:
		return 0.0
	return float((vertical_1 + vertical_2) / (2.0 * horizontal))


def landmark_asymmetry(face_landmark_68 : numpy.ndarray) -> float:
	left_eye = face_landmark_68[36:42].mean(axis = 0)
	right_eye = face_landmark_68[42:48].mean(axis = 0)
	nose = face_landmark_68[30]
	inter_eye = numpy.linalg.norm(left_eye - right_eye)
	if inter_eye <= 0:
		return 1.0
	left_offset = abs(float(left_eye[1] - nose[1]))
	right_offset = abs(float(right_eye[1] - nose[1]))
	return abs(left_offset - right_offset) / float(inter_eye)


def aligned_face_sharpness(vision_frame : VisionFrame, face : Face, template : str, crop_size : Tuple[int, int]) -> float:
	aligned_crop, _ = warp_face_by_face_landmark_5(vision_frame, face.landmark_set.get('5/68'), template, crop_size)
	gray = cv2.cvtColor(aligned_crop, cv2.COLOR_BGR2GRAY)
	return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def embedding_cosine_similarity(first_embedding : numpy.ndarray, second_embedding : numpy.ndarray) -> float:
	first_norm = first_embedding / max(numpy.linalg.norm(first_embedding), 1e-8)
	second_norm = second_embedding / max(numpy.linalg.norm(second_embedding), 1e-8)
	return float(numpy.dot(first_norm, second_norm))


def occlusion_center_visibility(occlusion_mask : numpy.ndarray) -> float:
	height, width = occlusion_mask.shape[:2]
	center_mask = occlusion_mask[height // 4:3 * height // 4, width // 4:3 * width // 4]
	return float(center_mask.mean())


def feature_regions_present(warped_crop : VisionFrame) -> Tuple[bool, dict]:
	region_peaks = {}
	for region in [ 'left-eye', 'right-eye', 'mouth' ]:
		region_mask = create_region_mask(warped_crop, [ region ])
		region_peaks[region] = round(float(region_mask.max()), 4)
	eye_peak = max(region_peaks['left-eye'], region_peaks['right-eye'])
	mouth_peak = region_peaks['mouth']
	present = eye_peak >= MIN_EYE_REGION_PEAK and mouth_peak >= MIN_MOUTH_REGION_PEAK
	return present, region_peaks


def validate_source_face(
	vision_frame : VisionFrame,
	face : Face,
	swap_template : str,
	swap_crop_size : Tuple[int, int]
) -> Tuple[bool, dict]:
	reasons : List[str] = []
	metrics = {
		'detector_score': round(float(face.score_set.get('detector', 0.0)), 4),
		'landmarker_score': round(float(face.score_set.get('landmarker', 0.0)), 4)
	}

	if face.score_set.get('detector', 0.0) < MIN_DETECTOR_SCORE:
		reasons.append('detector_below_threshold')

	landmarker_score = face.score_set.get('landmarker', 0.0)
	if landmarker_score < MIN_LANDMARKER_SCORE:
		reasons.append('landmarker_below_threshold')

	frame_height, frame_width = vision_frame.shape[:2]
	bounding_box = face.bounding_box
	face_width = bounding_box[2] - bounding_box[0]
	face_height = bounding_box[3] - bounding_box[1]
	short_side_ratio = min(face_width, face_height) / max(min(frame_width, frame_height), 1)
	metrics['face_short_side_ratio'] = round(float(short_side_ratio), 4)
	if short_side_ratio < MIN_FACE_SHORT_SIDE_RATIO:
		reasons.append('face_too_small')

	face_landmark_68 = face.landmark_set.get('68')
	if face_landmark_68 is not None and landmarker_score >= MIN_LANDMARKER_SCORE:
		left_ear = eye_aspect_ratio(face_landmark_68, LEFT_EYE_INDICES)
		right_ear = eye_aspect_ratio(face_landmark_68, RIGHT_EYE_INDICES)
		metrics['left_eye_aspect_ratio'] = round(left_ear, 4)
		metrics['right_eye_aspect_ratio'] = round(right_ear, 4)
		if left_ear < MIN_EYE_ASPECT_RATIO or right_ear < MIN_EYE_ASPECT_RATIO:
			reasons.append('eyes_low_aspect_ratio')

		asymmetry = landmark_asymmetry(face_landmark_68)
		metrics['landmark_asymmetry'] = round(asymmetry, 4)
		if asymmetry > MAX_LANDMARK_ASYMMETRY:
			reasons.append('landmark_asymmetry_high')

	embedding_5, _ = calculate_face_embedding(vision_frame, face.landmark_set.get('5'))
	embedding_refined, _ = calculate_face_embedding(vision_frame, face.landmark_set.get('5/68'))
	embedding_similarity = embedding_cosine_similarity(embedding_5, embedding_refined)
	metrics['embedding_cosine_similarity'] = round(embedding_similarity, 4)
	if embedding_similarity < MIN_EMBEDDING_COSINE_SIM:
		reasons.append('embedding_landmark_inconsistent')

	sharpness = aligned_face_sharpness(vision_frame, face, 'arcface_112_v2', (112, 112))
	metrics['aligned_sharpness'] = round(sharpness, 2)
	if sharpness < MIN_ALIGNED_SHARPNESS:
		reasons.append('aligned_crop_low_sharpness')

	warped_crop, _ = warp_face_by_face_landmark_5(vision_frame, face.landmark_set.get('5/68'), swap_template, swap_crop_size)
	occlusion_mask = create_occlusion_mask(warped_crop)
	occlusion_center_mean = occlusion_center_visibility(occlusion_mask)
	metrics['occlusion_center_mean'] = round(occlusion_center_mean, 4)
	if occlusion_center_mean < MIN_OCCLUSION_CENTER_MEAN:
		reasons.append('high_source_occlusion')

	regions_present, region_peaks = feature_regions_present(warped_crop)
	metrics['feature_region_peaks'] = region_peaks
	if not regions_present:
		reasons.append('features_not_visible')

	metrics['reasons'] = reasons
	return len(reasons) == 0, metrics


def validate_source_faces(
	source_samples : List[Tuple[VisionFrame, Face]],
	swap_template : str,
	swap_crop_size : Tuple[int, int]
) -> Tuple[bool, dict]:
	results = []

	for vision_frame, face in source_samples:
		passed, metrics = validate_source_face(vision_frame, face, swap_template, swap_crop_size)
		results.append(
		{
			'passed': passed,
			'metrics': metrics
		})

	if not results:
		return False, { 'reasons': [ 'no_source_face_detected' ] }

	aggregate_passed = all(result.get('passed') for result in results)
	aggregate_reasons = sorted(
	{
		reason
		for result in results
		for reason in result.get('metrics', {}).get('reasons', [])
	})
	return aggregate_passed, {
		'reasons': aggregate_reasons,
		'sources': [ result.get('metrics') for result in results ]
	}


def format_validation_error(report : dict) -> str:
	reasons = report.get('reasons', [])
	reason_text = ', '.join(reasons) if reasons else 'unknown'
	return SOURCE_QUALITY_MARKER + ': ' + reason_text


def format_validation_debug(report : dict) -> str:
	return json.dumps(report, separators = (',', ':'))
