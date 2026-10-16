"""A second bridge that cannot bind the command socket must leave the running bridge's socket in place. Fake serial only."""
import _bootstrap
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from test_bose_serial_drain import FAKE, bridge, health, wait_for  # noqa: E402


def test_failed_second_bind_keeps_the_live_socket():
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as second_tmp, bridge(tmp, 'ok') as (path, log, process):
        assert health(path)['mode'] == 'live'
        second_fake = FAKE.replace('mode,path,log=sys.argv[1:4]', 'mode,path,log,ir_state=sys.argv[1:5]')
        second_fake = second_fake.replace("str(Path(path).with_name('ir-state.json')),serial_port", 'ir_state,serial_port')
        second = subprocess.run([sys.executable, '-u', '-c', second_fake, 'ok', path, str(Path(second_tmp, 'second.log')),
                                 str(Path(second_tmp, 'ir-state.json'))], cwd=ROOT,
                                capture_output=True, text=True, timeout=20)
        assert second.returncode != 0, 'the second bridge cannot bind the same socket'
        assert 'already exists' in second.stderr, second.stderr
        assert os.path.exists(path), 'the running bridge socket was removed by the failed second start'
        assert health(path)['mode'] == 'live' and process.poll() is None
    print('A failed second bind leaves the running bridge socket and status intact')


def test_second_process_with_different_socket_is_locked_before_serial_open():
    with tempfile.TemporaryDirectory() as tmp, bridge(tmp, 'ok') as (path, _log, process):
        second_path = str(Path(tmp, 'second.sock'))
        second = subprocess.Popen([sys.executable, '-u', '-c', FAKE, 'ok', second_path, str(Path(tmp, 'second.log'))],
                                  cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            assert wait_for(lambda: second.poll() is not None or os.path.exists(second_path), 6)
            if second.poll() is None:
                second.terminate()
            out, err = second.communicate(timeout=4)
            assert second.returncode == 20, (out, err)
            assert 'already owns' in out, (out, err)
            assert 'Exact serial adapter open' not in out, out
            assert not os.path.exists(second_path)
            assert health(path)['serial_open'] and process.poll() is None
        finally:
            if second.poll() is None:
                second.kill()
                second.communicate(timeout=4)
    print('A second bridge cannot open serial even with a different command socket')


def test_leftover_socket_file_is_never_deleted_by_a_start():
    import socket
    import bose_bridge
    with tempfile.TemporaryDirectory() as tmp:
        leftover = str(Path(tmp, 'command.sock'))
        old = socket.socket(socket.AF_UNIX)
        old.bind(leftover); old.close()             # a socket file nobody listens on, as left by a crash
        server = socket.socket(socket.AF_UNIX)
        try:
            try:
                bose_bridge.bind_command_socket(server, leftover)
            except OSError as error:
                assert 'remove that file' in str(error), error
            else:
                raise AssertionError('a start must not replace an existing socket entry')
        finally:
            server.close()
        assert os.path.exists(leftover), 'the existing entry was deleted'
    print('A start never deletes an existing socket entry and says how to clear a stale one')


if __name__ == '__main__':
    test_failed_second_bind_keeps_the_live_socket()
    test_second_process_with_different_socket_is_locked_before_serial_open()
    test_leftover_socket_file_is_never_deleted_by_a_start()
