import os
import requests
from simple_log_factory.log_factory import log_factory

_url = os.environ.get('API_URL')
_logger = log_factory("Identify File", unique_handler_types=True)

def identify_file(full_path: str):
    response = requests.get(_url, params={'it': full_path})

    if response.status_code == 204:
        _logger.debug(f"Success identify request, but no useful data returned for: {full_path}")
        return None

    if response.ok:
        _logger.debug(f"Identify request successful for: {full_path}")
        return response.json()

    _logger.error(f"Identify request failed ({response.status_code}) for [{full_path}] with error: {response.text}")
    return None
