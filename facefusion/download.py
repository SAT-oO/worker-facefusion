import os
import subprocess
import json
import time
from functools import lru_cache
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from tqdm import tqdm

import facefusion.choices
from facefusion import curl_builder, logger, process_manager, state_manager, translator
from facefusion.filesystem import get_file_name, get_file_size, is_file, remove_file
from facefusion.hash_helper import validate_hash
from facefusion.types import Command, DownloadProvider, DownloadSet

DEBUG_LOG_PATH = '/Users/sat-oo/worker-facefusion/.cursor/debug-f92872.log'
DEBUG_SESSION_ID = 'f92872'


def _debug_log(run_id : str, hypothesis_id : str, location : str, message : str, data : dict) -> None:
	# #region agent log
	try:
		payload =\
		{
			'sessionId': DEBUG_SESSION_ID,
			'runId': run_id,
			'hypothesisId': hypothesis_id,
			'location': location,
			'message': message,
			'data': data,
			'timestamp': int(time.time() * 1000)
		}
		with open(DEBUG_LOG_PATH, 'a', encoding = 'utf-8') as debug_file:
			debug_file.write(json.dumps(payload) + '\n')
	except Exception:
		pass
	# #endregion


def open_curl(commands : List[Command]) -> subprocess.Popen[bytes]:
	commands = curl_builder.run(commands)
	return subprocess.Popen(commands, stdin = subprocess.PIPE, stdout = subprocess.PIPE)


def conditional_download(download_directory_path : str, urls : List[str]) -> None:
	for url in urls:
		download_file_name = os.path.basename(urlparse(url).path)
		download_file_path = os.path.join(download_directory_path, download_file_name)
		initial_size = get_file_size(download_file_path)
		download_size = get_static_download_size(url)

		if initial_size < download_size:
			with tqdm(total = download_size, initial = initial_size, desc = translator.get('downloading'), unit = 'B', unit_scale = True, unit_divisor = 1024, ascii = ' =', disable = state_manager.get_item('log_level') in [ 'warn', 'error' ]) as progress:
				commands = curl_builder.chain(
					curl_builder.download(url, download_file_path),
					curl_builder.set_timeout(5),
					curl_builder.set_retry(5)
				)
				open_curl(commands)
				current_size = initial_size
				progress.set_postfix(download_providers = state_manager.get_item('download_providers'), file_name = download_file_name)

				while current_size < download_size:
					if is_file(download_file_path):
						current_size = get_file_size(download_file_path)
						progress.update(current_size - progress.n)


@lru_cache(maxsize = 64)
def get_static_download_size(url : str) -> int:
	commands = curl_builder.chain(
		curl_builder.ping(url),
		curl_builder.set_timeout(5)
	)
	process = open_curl(commands)
	lines = reversed(process.stdout.readlines())

	for line in lines:
		__line__ = line.decode().lower()
		if 'content-length:' in __line__:
			_, content_length = __line__.split('content-length:')
			return int(content_length)

	return 0


@lru_cache(maxsize = 64)
def ping_static_url(url : str) -> bool:
	commands = curl_builder.chain(
		curl_builder.ping(url),
		curl_builder.set_timeout(5)
	)
	process = open_curl(commands)
	process.communicate()
	return process.returncode == 0


def conditional_download_hashes(hash_set : DownloadSet) -> bool:
	hash_paths = [ hash_set.get(hash_key).get('path') for hash_key in hash_set.keys() ]

	process_manager.check()
	_, invalid_hash_paths = validate_hash_paths(hash_paths)
	if invalid_hash_paths:
		for index in hash_set:
			if hash_set.get(index).get('path') in invalid_hash_paths:
				invalid_hash_url = hash_set.get(index).get('url')
				if invalid_hash_url:
					download_directory_path = os.path.dirname(hash_set.get(index).get('path'))
					conditional_download(download_directory_path, [ invalid_hash_url ])

	valid_hash_paths, invalid_hash_paths = validate_hash_paths(hash_paths)

	for valid_hash_path in valid_hash_paths:
		valid_hash_file_name = get_file_name(valid_hash_path)
		logger.debug(translator.get('validating_hash_succeeded').format(hash_file_name = valid_hash_file_name), __name__)
	for invalid_hash_path in invalid_hash_paths:
		invalid_hash_file_name = get_file_name(invalid_hash_path)
		logger.error(translator.get('validating_hash_failed').format(hash_file_name = invalid_hash_file_name), __name__)

	if not invalid_hash_paths:
		process_manager.end()
	return not invalid_hash_paths


def conditional_download_sources(source_set : DownloadSet) -> bool:
	source_paths = [ source_set.get(source_key).get('path') for source_key in source_set.keys() ]

	process_manager.check()
	_, invalid_source_paths = validate_source_paths(source_paths)
	# #region agent log
	if any(source_path.endswith('fan_68_5.onnx') for source_path in source_paths) or any(source_path.endswith('peppa_wutz.onnx') for source_path in source_paths):
		_debug_log('pre-fix', 'H5', 'facefusion/download.py:134', 'initial source validation', { 'source_paths': source_paths, 'invalid_source_paths': invalid_source_paths })
	# #endregion
	if invalid_source_paths:
		for index in source_set:
			if source_set.get(index).get('path') in invalid_source_paths:
				invalid_source_url = source_set.get(index).get('url')
				if invalid_source_url:
					download_directory_path = os.path.dirname(source_set.get(index).get('path'))
					conditional_download(download_directory_path, [ invalid_source_url ])

	valid_source_paths, invalid_source_paths = validate_source_paths(source_paths)
	# #region agent log
	if any(source_path.endswith('fan_68_5.onnx') for source_path in source_paths) or any(source_path.endswith('peppa_wutz.onnx') for source_path in source_paths):
		_debug_log('pre-fix', 'H5', 'facefusion/download.py:145', 'post-download source validation', { 'valid_source_paths': valid_source_paths, 'invalid_source_paths': invalid_source_paths })
	# #endregion

	for valid_source_path in valid_source_paths:
		valid_source_file_name = get_file_name(valid_source_path)
		logger.debug(translator.get('validating_source_succeeded').format(source_file_name = valid_source_file_name), __name__)
	for invalid_source_path in invalid_source_paths:
		invalid_source_file_name = get_file_name(invalid_source_path)
		logger.error(translator.get('validating_source_failed').format(source_file_name = invalid_source_file_name), __name__)

		if remove_file(invalid_source_path):
			logger.error(translator.get('deleting_corrupt_source').format(source_file_name = invalid_source_file_name), __name__)

	if not invalid_source_paths:
		process_manager.end()
	return not invalid_source_paths


def validate_hash_paths(hash_paths : List[str]) -> Tuple[List[str], List[str]]:
	valid_hash_paths = []
	invalid_hash_paths = []

	for hash_path in hash_paths:
		if is_file(hash_path):
			valid_hash_paths.append(hash_path)
		else:
			invalid_hash_paths.append(hash_path)

	return valid_hash_paths, invalid_hash_paths


def validate_source_paths(source_paths : List[str]) -> Tuple[List[str], List[str]]:
	valid_source_paths = []
	invalid_source_paths = []

	for source_path in source_paths:
		if validate_hash(source_path):
			valid_source_paths.append(source_path)
		else:
			invalid_source_paths.append(source_path)

	return valid_source_paths, invalid_source_paths


def resolve_download_url(base_name : str, file_name : str) -> Optional[str]:
	download_providers = state_manager.get_item('download_providers')

	for download_provider in download_providers:
		download_url = resolve_download_url_by_provider(download_provider, base_name, file_name)
		if download_url:
			return download_url

	return None


def resolve_download_url_by_provider(download_provider : DownloadProvider, base_name : str, file_name : str) -> Optional[str]:
	download_provider_value = facefusion.choices.download_provider_set.get(download_provider)

	for download_provider_url in download_provider_value.get('urls'):
		if ping_static_url(download_provider_url):
			return download_provider_url + download_provider_value.get('path').format(base_name = base_name, file_name = file_name)

	return None
