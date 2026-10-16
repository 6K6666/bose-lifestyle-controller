"""Fresh-install rehearsal: installer, saved config, dry-run and live bridge, CLI and uninstall, in a temp root.

Nothing here touches the real filesystem root, systemd, udev, a real serial port or a socket outside its temp
directory. The bridge runs as a subprocess with a fake serial module that records every open and write.
Run: python3 -B tests/test_bose_fresh_install.py
"""
import _bootstrap
import base64
import json
import os
import re
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'deploy'))
from bose_config import ZONES, SOURCES, parse_config  # noqa: E402
from bose_request import request  # noqa: E402

INSTALL = ROOT / 'deploy' / 'install-bridge.sh'
DOC_UNINSTALL = ROOT / 'docs' / 'uninstall.md'
DOC_INSTALL = ROOT / 'docs' / 'install.md'
KEY_LITERAL = 'ssh-ed25519 AAAAPLACEHOLDERKEYBLOB'   # used only if cryptography is not installed
SERIAL_PATH = '/dev/serial/by-id/usb-example-if00-port0'
ACTION_REF = re.compile(r'^room-[a-d]-')

# A fake serial module: every open and every write is logged, and nothing reaches a real device.
BRIDGE = r'''
import sys, types
inst, sock, state, mode, log, serial_path = sys.argv[1:7]
sys.path.insert(0, inst)
def record(text):
    with open(log, 'a') as handle:
        handle.write(text + '\n')
class Port:
    def __init__(self, **kwargs): self.pending = b''
    def open(self): record('open ' + serial_path)
    def close(self): record('close')
    in_waiting = 0
    def read(self, n):
        out, self.pending = self.pending[:n], self.pending[n:]
        return out
    def write(self, data):
        record('TX ' + data.hex())
        return len(data)
    def flush(self): pass
sys.modules['serial'] = types.SimpleNamespace(Serial=Port)
sys.modules['evdev'] = None
from bose_bridge import serve
serve(sock, mode == 'dry', ir_state=state, serial_port=serial_path)
'''

# The CLI with the socket, state and device-listing folders passed in: the real defaults are never read.
CLI = ('import sys; sys.path.insert(0, sys.argv[1]); import bose_cli; '
       'sys.exit(bose_cli.main(sys.argv[6:], socket_path=sys.argv[2], state_dir=sys.argv[3], '
       'by_id_dir=sys.argv[4], input_dir=sys.argv[5]))')


def public_key():
    """A throwaway ed25519 public key in OpenSSH form, from cryptography when it is installed."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    except ImportError:
        return KEY_LITERAL
    raw = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    blob = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', len(raw)) + raw
    return 'ssh-ed25519 ' + base64.b64encode(blob).decode('ascii')


def documented_install_paths():
    """Absolute paths the uninstall page's table says the installer creates. Rows for runtime, receiver-only,
    bridge-written and operator-made paths are left out: they do not exist after a plain install."""
    text = DOC_UNINSTALL.read_text(encoding='utf-8')
    section = text.split('## What the installer created', 1)[1].split('## Check that it is gone', 1)[0]
    skip = ('written by', 'runtime', 'receiver only', 'made by you', '__pycache__')
    dirs, files = set(), set()
    for line in section.splitlines():
        if not line.startswith('|') or line.startswith('|---') or line.startswith('| Created'):
            continue
        cell = line.split('|')[1]
        if any(word in cell for word in skip):
            continue
        base = None                       # a bare name such as bose_cli.py belongs to the last directory row
        for token in re.findall(r'`([^`]+)`', cell):
            if '=' in token or '"' in token:
                continue                  # the authorized_keys line itself, not a path
            if token.startswith('/'):
                if token.endswith('/'):
                    base = token.rstrip('/')
                    dirs.add(base)
                else:
                    files.add(token)
            elif base is not None:
                files.add(base + '/' + token)
    return dirs, files


def rel(path, root):
    return Path(path).relative_to(root).as_posix()


class FreshInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='bfi'))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / 'root'
        self.inst = self.root / 'opt' / 'bose-lifestyle'
        self.state = self.root / 'var' / 'lib' / 'bose-control'
        self.sock = self.tmp / 's.sock'            # short path: unix socket names are length-limited
        self.log = self.tmp / 'serial.log'
        self.byid = self.tmp / 'by-id'
        self.input = self.tmp / 'input'
        self.byid.mkdir()
        self.input.mkdir()
        node = self.root / SERIAL_PATH.lstrip('/')    # the device node the installer looks up under --root
        node.parent.mkdir(parents=True)
        node.write_bytes(b'')
        (self.root / 'home' / 'bose').mkdir(parents=True)   # the account's home exists before the installer runs
        self.pybin = self.tmp / 'pybin'
        self.pybin.mkdir()
        (self.pybin / 'python3').symlink_to(sys.executable)
        self.key = public_key()

    # ---- helpers -------------------------------------------------------------------------------------
    def run_installer(self):
        env = dict(os.environ)
        env['PATH'] = os.pathsep.join([str(self.pybin), env['PATH']])
        cmd = ['sh', str(INSTALL), '--root', str(self.root), '--public-key', self.key, '--no-systemctl',
               '--skip-prereq-check', '--serial-device', SERIAL_PATH]
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(ROOT))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def cli(self, *args):
        cmd = [sys.executable, '-I', '-B', '-c', CLI, str(self.inst), str(self.sock), str(self.state),
               str(self.byid), str(self.input), *args]
        return subprocess.run(cmd, capture_output=True, text=True, cwd=str(self.tmp), timeout=30)

    def start_bridge(self, mode):
        self.log.write_text('')
        proc = subprocess.Popen([sys.executable, '-B', '-u', '-c', BRIDGE, str(self.inst), str(self.sock),
                                 str(self.state / 'ir-state.json'), mode, str(self.log), SERIAL_PATH],
                                cwd=str(self.tmp), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(300):
            if self.sock.exists():
                break
            if proc.poll() is not None:
                raise AssertionError(proc.communicate())
            time.sleep(0.02)
        self.assertTrue(self.sock.exists(), 'bridge did not bind its socket')
        time.sleep(0.2)
        return proc

    def stop_bridge(self, proc):
        proc.terminate()
        out, err = proc.communicate(timeout=10)
        self.assertEqual(err, '', 'bridge wrote to stderr')
        return out

    def status(self):
        return json.loads(request(str(self.sock), b'status\n'))

    def log_lines(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def config_bytes(self):
        return (self.state / 'config.json').read_bytes()

    def tree(self):
        """Every directory and file under the temp root, except the device nodes this test created."""
        found = {}
        for path in self.root.rglob('*'):
            if path.relative_to(self.root).parts[0] == 'dev':
                continue
            found[rel(path, self.root)] = path
        return found

    def install_fresh(self):
        output = self.run_installer()
        self.assertIn('Created ' + str(self.state / 'config.json') + ': every room is off', output)
        return output


class TestFreshInstallToConfiguredBridge(FreshInstall):
    def test_fresh_install_to_configured_bridge_in_temp_root(self):
        self.install_fresh()

        # 1. The created tree is exactly what the uninstall page lists (the installed-at-install rows).
        dirs, files = documented_install_paths()
        expected_files = {rel(self.root / p.lstrip('/'), self.root) for p in files}
        expected_dirs = {rel(self.root / p.lstrip('/'), self.root) for p in dirs}
        for f in list(expected_files):
            for parent in Path(f).parents:
                if parent.as_posix() != '.':
                    expected_dirs.add(parent.as_posix())
        found = self.tree()
        found_files = {p for p, path in found.items() if path.is_file()}
        found_dirs = {p for p, path in found.items() if path.is_dir()}
        self.assertEqual(found_files, expected_files)
        self.assertEqual(found_dirs, expected_dirs)

        # 2. Modes: state dir 0700, ssh dir 0700, authorized_keys 0600, config 0600.
        ssh = self.root / 'home' / 'bose' / '.ssh'
        self.assertEqual(stat.S_IMODE(os.stat(self.state).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(ssh).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(ssh / 'authorized_keys').st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.state / 'config.json').st_mode), 0o600)

        # 3. The default config: every zone off, no source connected, nothing unverified, IR target a.
        config = parse_config(self.config_bytes())
        self.assertEqual({z: config['zones'][z]['enabled'] for z in ZONES}, {z: False for z in ZONES})
        self.assertEqual(set(config['sources'].values()), {False})
        self.assertEqual(set(config['sources']), set(SOURCES))
        self.assertEqual(set(config['unverified'].values()), {False})
        self.assertEqual(config['ir'], {'zone': 'a'})

        # 4. Dry-run bridge: configured, only identify allowed, no port opened, nothing transmitted.
        proc = self.start_bridge('dry')
        try:
            status = self.status()
            self.assertEqual(status['bridge']['config'], 'ok')
            self.assertFalse(status['bridge']['serial_open'])
            self.assertEqual(status['bridge']['mode'], 'dry-run')
            # identify is not a catalog action: it is gated by the live-mode check in serve(), so no catalog action
            # is allowed while the config is untouched.
            allowed = {name for name, a in status['actions'].items() if a['allowed']}
            self.assertEqual(allowed, set())
            cli_status = self.cli('status')
            self.assertEqual(cli_status.returncode, 0, cli_status.stderr)
            self.assertIn('Bridge: running (dry-run mode); serial port closed', cli_status.stdout)
            self.assertIn('Last command sent: none recorded', cli_status.stdout)
        finally:
            self.stop_bridge(proc)
        self.assertEqual(self.log_lines(), [])

        # 5. Offline changes through the CLI (no bridge running): zone a, source aux, and an experimental no-op.
        zone_a = self.cli('zone', 'a', 'on')
        self.assertEqual(zone_a.returncode, 0, zone_a.stderr)
        self.assertIn('Saved. It applies when the bridge next starts.', zone_a.stdout)
        self.assertEqual(self.cli('source', 'aux', 'on').returncode, 0)
        before_noop = self.config_bytes()
        noop = self.cli('experimental', 'd', 'off')
        self.assertEqual(noop.returncode, 0, noop.stderr)
        self.assertEqual(self.config_bytes(), before_noop)
        parse_config(self.config_bytes())

        # 6. Restart dry-run: the config holds zone a and the aux input, but dry-run allows no action (status allowed is false).
        proc = self.start_bridge('dry')
        try:
            status = self.status()
            self.assertEqual(status['config']['zones']['a']['enabled'], True)
            self.assertIs(status['config']['sources']['aux'], True)
            self.assertEqual({name for name, a in status['actions'].items() if a['allowed']}, set())
        finally:
            self.stop_bridge(proc)
        self.assertEqual(self.log_lines(), [])

        # 7. Live bridge on the fake serial: zone b and the IR target through the CLI, and no frame written.
        proc = self.start_bridge('live')
        try:
            self.assertIn(f'open {SERIAL_PATH}', self.log_lines())
            zone_b = self.cli('zone', 'b', 'on')
            self.assertEqual(zone_b.returncode, 0, zone_b.stderr)
            self.assertIn('Saved.', zone_b.stdout)
            self.assertTrue(self.status()['config']['zones']['b']['enabled'])
            target = self.cli('ir', 'target', 'b')
            self.assertEqual(target.returncode, 0, target.stderr)
            self.assertEqual(self.status()['config']['ir'], {'zone': 'b'})
        finally:
            out = self.stop_bridge(proc)
        self.assertNotIn('TX', ' '.join(self.log_lines()))
        self.assertNotIn('TX', out)
        self.assertEqual(json.loads(self.config_bytes())['ir'], {'zone': 'b'})

        # 8. A re-run after the user's changes and the bridge's own files: the saved config is never overwritten.
        saved = self.config_bytes()
        rerun = self.run_installer()
        self.assertEqual(self.config_bytes(), saved)
        self.assertIn('config.json left unchanged', rerun)
        self.assertTrue(rerun.rstrip().endswith('already installed; nothing changed'), rerun)


class TestInstallerRerun(FreshInstall):
    def test_installer_rerun_changes_nothing(self):
        self.install_fresh()

        def snapshot():
            # Modes and bytes for everything, mtimes for files. Directory mtimes are left out: the installer's
            # temp files are created beside their targets and removed, which touches a directory without a change.
            result = {}
            for name, path in self.tree().items():
                st = path.stat()
                result[name] = (stat.S_IMODE(st.st_mode), path.read_bytes() if path.is_file() else None,
                                st.st_mtime_ns if path.is_file() else None)
            return result
        before = snapshot()
        output = self.run_installer()
        self.assertIn('config.json left unchanged', output)
        self.assertTrue(output.rstrip().endswith('already installed; nothing changed'), output)
        self.assertEqual(snapshot(), before)


def doc_bose_commands(section_title):
    """The bose-lifestyle command lines in fenced blocks of one docs/install.md section, in order."""
    text = DOC_INSTALL.read_text(encoding='utf-8')
    section = text.split(section_title, 1)[1].split('\n## ', 1)[0]
    commands, inside = [], False
    for line in section.splitlines():
        if line.strip().startswith('```'):
            inside = not inside
            continue
        words = shlex.split(line) if inside and line.strip() else []
        if any(w == 'bose-lifestyle' or w.endswith('/bose-lifestyle') for w in words):
            commands.append(line.strip())
    return commands


class TestCommandLineRoute(FreshInstall):
    HARDWARE = ('verify', 'select', 'learn', 'button', 'remote', 'forget')

    def test_cli_only_route_matches_docs_install_commands(self):
        self.install_fresh()
        commands = doc_bose_commands('## Route B: Command line only')
        self.assertTrue(commands, 'the command line route lists no commands')
        for line in commands:
            words = shlex.split(line)
            index = next(i for i, w in enumerate(words) if w == 'bose-lifestyle' or w.endswith('/bose-lifestyle'))
            args = words[index + 1:]
            if args[:1] == ['ir'] and args[1:2] and args[1] in self.HARDWARE:
                # Needs a receiver, a learned remote or the bridge: only the help text is checked here.
                result = self.cli('ir', args[1], '--help')
                self.assertEqual(result.returncode, 0, line + ' ' + result.stderr)
                continue
            result = self.cli(*args)
            if args == ['init']:
                # The installer already created the configuration: init reports that and changes nothing.
                self.assertEqual(result.returncode, 1, line)
                self.assertIn('A configuration already exists. Nothing was changed.', result.stderr)
            else:
                self.assertEqual(result.returncode, 0, line + ' ' + result.stderr)
        self.assertNotIn('Zones:', self.cli('status').stdout)
        self.assertIn('Bridge: not running (saved configuration)', self.cli('status').stdout)


class TestUninstallPathsRemovable(FreshInstall):
    def test_uninstall_doc_paths_all_removable(self):
        self.install_fresh()
        # The runtime and bridge-written files exist too, as they do after the bridge has run.
        (self.state / 'ir-state.json').write_text('{}')
        (self.state / 'events.jsonl').write_text('')
        (self.state / 'ir-config.json').write_text('{}')
        self.run_uninstall_steps()
        found = self.tree()
        self.assertEqual({p for p, path in found.items() if path.is_file()}, set(), 'files left')
        # Directories the page does not remove: the system folders above the removed paths and the bose user's home.
        self.assertEqual({p for p, path in found.items() if path.is_dir()},
                         {'etc', 'etc/systemd', 'etc/systemd/system', 'home', 'home/bose', 'opt', 'var', 'var/lib'})
        self.assertFalse(self.inst.exists())
        self.assertFalse(self.state.exists())

    def run_uninstall_steps(self):
        """Follow the page's command lines literally under the temp root: rm, rm -f and rm -r of listed paths.
        systemctl and udevadm lines are not run (the temp root has no systemd or udev). Only absolute paths are
        mapped under the temp root; ~ paths are the operator's and are skipped."""
        in_block = False
        for line in DOC_UNINSTALL.read_text(encoding='utf-8').splitlines():
            if line.strip().startswith('```'):
                in_block = not in_block
                continue
            if not in_block or not line.strip():
                continue
            words = shlex.split(line.strip())
            if words[0] == 'sudo':
                words = words[1:]
            if not words or words[0] != 'rm':
                continue
            recursive = any(w in ('-r', '-rf', '-fr') for w in words[1:] if w.startswith('-'))
            for path in (w for w in words[1:] if not w.startswith('-')):
                if not path.startswith('/'):
                    continue
                target = self.root / path.lstrip('/')
                if target.is_dir() and not target.is_symlink():
                    if recursive:
                        shutil.rmtree(target)
                elif target.exists() or target.is_symlink():
                    target.unlink()
        # The authorized key line: the page says to delete that line, and the file if it is then empty.
        keys = self.root / 'home' / 'bose' / '.ssh' / 'authorized_keys'
        if keys.exists():
            kept = [ln for ln in keys.read_text().splitlines()
                    if not ln.startswith('command="/opt/bose-lifestyle/bose_request.py",restrict')]
            if kept:
                keys.write_text('\n'.join(kept) + '\n')
            else:
                keys.unlink()
        ssh = self.root / 'home' / 'bose' / '.ssh'
        if ssh.is_dir() and not any(ssh.iterdir()):
            ssh.rmdir()


if __name__ == '__main__':
    unittest.main(verbosity=2)
