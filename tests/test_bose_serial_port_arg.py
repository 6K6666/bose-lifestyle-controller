"""--serial-port: argument validation, unconfigured start, configured port reaches the port object.

Fake serial module only; the real serial import is never reached. Run: python3 -B tests/test_bose_serial_port_arg.py
"""
import _bootstrap
import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from helpers import write_config  # noqa: E402

with patch.dict(sys.modules, {'serial': None, 'evdev': None}):
    import bose_bridge  # noqa: E402
from bose_bridge import serial_port_arg  # noqa: E402
from bose_request import request  # noqa: E402

PORT = '/dev/serial/by-id/usb-example-if00-port0'

# Fake pyserial: every construction and open is recorded in a log file; open() fails when the log says so.
FAKE_SERIAL = r'''
import sys, types
log = sys.argv[2]
def record(text):
    with open(log, 'a') as handle:
        handle.write(text + '\n')
class Serial:
    def __init__(self, **kwargs):
        record('construct')
        self.port = None
        self.pending = b''
        self.in_waiting = 0
    def open(self):
        record('open ' + repr(self.port))
    def close(self):
        record('close')
    def read(self, n):
        return b''
    def write(self, data):
        record('TX ' + data.hex())
        return len(data)
    def flush(self):
        pass
sys.modules['serial'] = types.SimpleNamespace(Serial=Serial)
sys.modules['evdev'] = None
from bose_bridge import serve
serial_port = sys.argv[3] if sys.argv[3] != '-' else None
serve(sys.argv[1], False, None, sys.argv[1] + '.state', serial_port=serial_port)
'''


def run_bridge(tmp, serial_port):
    path = os.path.join(tmp, 'command.sock')
    log = os.path.join(tmp, 'serial.log')
    write_config(Path(tmp, 'config.json'))
    proc = subprocess.Popen([sys.executable, '-u', '-c', FAKE_SERIAL, path, log, serial_port or '-'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(ROOT))
    try:
        for _ in range(200):
            if os.path.exists(path):
                break
            if proc.poll() is not None:
                raise AssertionError(proc.stderr.read())
            time.sleep(.02)
        status = json.loads(request(path, b'status\n'))
        identify = request(path, b'identify\n')
        events = request(path, b'events\n')
    finally:
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=5)
    lines = Path(log).read_text().splitlines() if os.path.exists(log) else []
    return status, identify, events, lines, out, err


class SerialPortArgTests(unittest.TestCase):
    def test_argument_validated(self):
        for good in [PORT, '/dev/serial/by-id/usb-example-if00-port0']:
            self.assertEqual(serial_port_arg(good), good)
        for bad in ['relative/usb-example', '/dev/ttyUSB0', '/dev/serial/by-id/', '/dev/serial/by-id/../ttyUSB0',
                    '/dev/serial/by-id/usb-example\n', '/dev/serial/by-id/usb-example;rm', '']:
            with self.assertRaises(argparse.ArgumentTypeError, msg=repr(bad)):
                serial_port_arg(bad)

    def test_bad_argument_exits_2_before_anything_starts(self):
        res = subprocess.run([sys.executable, '-B', str(ROOT / 'bose_bridge.py'), '--serial-port', '/dev/ttyUSB0'],
                             capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertIn('--serial-port', res.stderr)

    def test_unconfigured_start_does_not_open_or_transmit(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, identify, events, lines, out, err = run_bridge(tmp, None)
            self.assertEqual(err.strip(), '', err)
            self.assertFalse(status['bridge']['serial_open'])
            self.assertEqual(identify, 'rejected')
            self.assertFalse([line for line in lines if line.startswith(('construct', 'open', 'TX'))], lines)
            self.assertIn('serial port not configured', events)
            self.assertIn('serial port not configured', out + events)

    def test_configured_port_reaches_port_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, identify, events, lines, out, err = run_bridge(tmp, PORT)
            self.assertEqual(err.strip(), '', err)
            self.assertIn("open '" + PORT + "'", lines)
            self.assertTrue(status['bridge']['serial_open'], status)
            self.assertNotIn('serial port not configured', events)


if __name__ == '__main__':
    unittest.main(verbosity=1)
