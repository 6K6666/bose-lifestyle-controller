"""Test helpers: a generic baseline config and a writer that stores it the way the installer does (mode 0600)."""
import json
import os
from pathlib import Path

# A test baseline that allows exactly the verified live commands: zones A-C on, zone D off, common sources on,
# nothing unverified. Pinned by test_bose_catalog.py.
BASELINE_CONFIG = {
    'schema': 1,
    'zones': {'a': {'enabled': True}, 'b': {'enabled': True},
              'c': {'enabled': True}, 'd': {'enabled': False}},
    'sources': {'am': True, 'fm': False, 'cd': False, 'aux': True,
                'video-1': True, 'video-2': True, 'tape': False},
    'unverified': {'a': False, 'b': False, 'c': False, 'd': False},
    'ir': {'zone': 'a'},
}


def write_config(path: Path, config: dict = None) -> None:
    data = json.dumps(BASELINE_CONFIG if config is None else config).encode('utf-8')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as out:
        out.write(data)
    os.chmod(path, 0o600)
