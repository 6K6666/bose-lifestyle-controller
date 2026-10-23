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


# Replies to the two live status queries (MM 2A,0 volume, MM 2B,00 selected input), captured from the control unit on
# 2026-10-21. Fake serial ports answer these so the bridge's telemetry poll can run; they log the query as "QX", not
# "TX", so a suite's count of transmitted commands stays a count of commands.
QUERY_REPLIES = {
    b'MM 2A,0\r': b'\r\r\r\r>.0A0024222337\r',
    b'MM 2B,00\r': b'\r\r\r\r>.0B0004030401\r',
}


def write_config(path: Path, config: dict = None) -> None:
    data = json.dumps(BASELINE_CONFIG if config is None else config).encode('utf-8')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as out:
        out.write(data)
    os.chmod(path, 0o600)
