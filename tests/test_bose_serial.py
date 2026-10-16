"""Offline byte/default/dry-run check; serial import or port access fails."""
import _bootstrap
import contextlib
import importlib.util
import io
import sys
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('bose_serial', Path(__file__).resolve().parents[1] / 'bose_serial.py')
bose = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'serial': None}):
    spec.loader.exec_module(bose)
    assert bose.VOLUME_DOWN == bytes.fromhex('4d 4d 20 34 34 2c 34 32 2c 30 30 0a')
    assert bose.DEFAULT_PORT == '/dev/serial/by-id/usb-PLACEHOLDER-if00-port0'
    assert bose.DEFAULT_BAUD == 4800
    for action, expected in [('volume-down', bose.VOLUME_DOWN), ('loopback', b'BOSE\r')]:
        output = io.StringIO()
        with patch.object(sys, 'argv', ['bose_serial.py', action, '--dry-run']), contextlib.redirect_stdout(output):
            bose.main()
        assert expected.hex(' ') in output.getvalue()
        assert 'no port opened' in output.getvalue()
print('Offline byte/default/dry-run check passed')
# Normal volume control has one owner, with no direct-port fallback.
with patch.dict(sys.modules, {'serial': None}), patch.object(sys, 'argv', ['bose_serial.py','volume-down']), patch('bose_request.request', return_value='rejected') as routed:
    try: bose.main()
    except SystemExit: pass
    assert routed.call_count==1, 'ordinary volume control bypassed bridge'
print('Ordinary CLI volume routes through bridge only')
