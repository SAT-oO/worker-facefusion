import json
import os
import time
from typing import Any, Dict, Optional

SESSION_ID = '990aa8'
DEFAULT_LOG_PATH = '/Users/sat-oo/worker-facefusion/.cursor/debug-990aa8.log'
CONTAINER_LOG_PATH = '/tmp/facefusion_agent_debug.ndjson'


def agent_debug_log(
	hypothesis_id : str,
	location : str,
	message : str,
	data : Optional[Dict[str, Any]] = None,
	run_id : str = 'pre-fix'
) -> None:
	payload = {
		'sessionId': SESSION_ID,
		'runId': run_id,
		'hypothesisId': hypothesis_id,
		'location': location,
		'message': message,
		'data': data or {},
		'timestamp': int(time.time() * 1000)
	}
	line = json.dumps(payload) + '\n'
	for path in (
		os.environ.get('AGENT_DEBUG_LOG_PATH', DEFAULT_LOG_PATH),
		CONTAINER_LOG_PATH
	):
		try:
			parent = os.path.dirname(path)
			if parent:
				os.makedirs(parent, exist_ok=True)
			with open(path, 'a', encoding = 'utf-8') as file_handle:
				file_handle.write(line)
		except OSError:
			pass


def read_container_debug_lines() -> list:
	try:
		with open(CONTAINER_LOG_PATH, encoding = 'utf-8') as file_handle:
			return [ line.strip() for line in file_handle if line.strip() ]
	except OSError:
		return []
