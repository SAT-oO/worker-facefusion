import os
import json
import time
import zlib
from typing import Optional

from facefusion.filesystem import get_file_name, is_file


def create_hash(content : bytes) -> str:
	return format(zlib.crc32(content), '08x')


def validate_hash(validate_path : str) -> bool:
	hash_path = get_hash_path(validate_path)

	if is_file(hash_path):
		with open(hash_path) as hash_file:
			hash_content = hash_file.read()

		with open(validate_path, 'rb') as validate_file:
			validate_content = validate_file.read()
		actual_hash = create_hash(validate_content)
		is_valid = actual_hash == hash_content
		# #region agent log
		if validate_path.endswith('fan_68_5.onnx') or validate_path.endswith('peppa_wutz.onnx'):
			try:
				payload =\
				{
					'sessionId': 'f92872',
					'runId': 'pre-fix',
					'hypothesisId': 'H6',
					'location': 'facefusion/hash_helper.py:30',
					'message': 'hash comparison for critical landmarker source',
					'data':\
					{
						'validate_path': validate_path,
						'hash_path': hash_path,
						'actual_hash': actual_hash,
						'expected_hash': hash_content.strip(),
						'expected_hash_raw_len': len(hash_content),
						'validate_size': len(validate_content),
						'is_valid': is_valid
					},
					'timestamp': int(time.time() * 1000)
				}
				with open('/Users/sat-oo/worker-facefusion/.cursor/debug-f92872.log', 'a', encoding = 'utf-8') as debug_file:
					debug_file.write(json.dumps(payload) + '\n')
			except Exception:
				pass
		# #endregion
		return is_valid
	return False


def get_hash_path(validate_path : str) -> Optional[str]:
	if is_file(validate_path):
		validate_directory_path, file_name_and_extension = os.path.split(validate_path)
		validate_file_name = get_file_name(file_name_and_extension)

		return os.path.join(validate_directory_path, validate_file_name + '.hash')
	return None
