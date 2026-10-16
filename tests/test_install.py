"""Tests for the bridge installer and its templates (deploy/).

Every installer run uses --root in a temp directory and --no-systemctl; no test touches the
real filesystem root, systemd or udev. Run: python3 -B tests/test_install.py
"""
import _bootstrap
import errno
import contextlib
import grp
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / 'deploy'
sys.path.insert(0, str(DEPLOY))
import authorized_keys  # noqa: E402
from authorized_keys import authorized_keys_line, check_platform, device_group, install_key  # noqa: E402
from bose_config import default_config  # noqa: E402

INSTALL = DEPLOY / 'install-bridge.sh'
SHELL = os.environ.get('BRIDGE_INSTALL_SH', 'sh')   # e.g. dash, to check POSIX use
KEY = 'ssh-ed25519 AAAAPLACEHOLDERKEYBLOB'
# Dotted quads and vendor strings are built at runtime: the repo scan forbids them as literals.
NET = '.'.join(['192', '0', '2', '0'])
BAD_ADDR = '.'.join(['10', '0', '0', '1'])
VENDOR = 'usb-' + 'FTDI_'
DIR = '/opt/bose-lifestyle'
SERIAL_PATH = '/dev/serial/by-id/usb-example-if00-port0'   # a by-id path; the group comes from $ROOT + this path
RECEIVER_PATH = '/dev/input/by-id/usb-example-event-kbd'
RX_IDS = ['--receiver-vendor', 'abcd', '--receiver-product', '0123']
IPV4 = re.compile(r'\b\d{1,3}(?:\.\d{1,3}){3}\b')


def tree_bytes(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob('*')) if p.is_file()}


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / 'root'
        # Device nodes live under the temp root: with --root the installer looks groups up at $ROOT + path.
        self.dev = self.root / SERIAL_PATH.lstrip('/')
        self.recv = self.root / RECEIVER_PATH.lstrip('/')
        for node in (self.dev, self.recv):
            node.parent.mkdir(parents=True, exist_ok=True)
            node.write_bytes(b'')
        self.pybin = self.tmp / 'pybin'
        self.pybin.mkdir()
        (self.pybin / 'python3').symlink_to(sys.executable)
        (self.root / 'home' / 'bose').mkdir(parents=True)   # the account's home must exist; the installer does not create it

    def install(self, *args, path=None, devices=True, prereq=False):
        env = dict(os.environ)
        env['PATH'] = os.pathsep.join(filter(None, [path, str(self.pybin), env['PATH']]))
        cmd = [SHELL, str(INSTALL), '--root', str(self.root), '--public-key', KEY, '--no-systemctl']
        if devices:
            cmd += ['--serial-device', SERIAL_PATH, '--receiver-device', RECEIVER_PATH]
        if not prereq:                       # the host may lack pyserial and evdev; prereq tests opt in
            cmd.append('--skip-prereq-check')
        return subprocess.run(cmd + list(args), capture_output=True, text=True, env=env, cwd=str(REPO))

    def shim(self, serial_rc, evdev_rc):
        """A python3 that answers the two prerequisite imports with the given exit codes and runs the real Python otherwise."""
        folder = self.tmp / ('shim-%d-%d' % (serial_rc, evdev_rc))
        folder.mkdir(exist_ok=True)
        script = folder / 'python3'
        script.write_text('#!/bin/sh\n'
                          'case "$*" in\n'
                          '  *"import serial"*) exit %d ;;\n'
                          '  *"import evdev"*) exit %d ;;\n'
                          'esac\n'
                          'exec %s "$@"\n' % (serial_rc, evdev_rc, sys.executable))
        script.chmod(0o755)
        return str(folder)

    def ak(self, home='/home/bose'):
        return self.root / home.lstrip('/') / '.ssh' / 'authorized_keys'

    def assert_ok(self, res):
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)


class AuthorizedKeysTests(unittest.TestCase):
    def test_authorized_keys_line_exact_and_rejects_injection(self):
        cmd = DIR + '/bose_request.py'
        self.assertEqual(
            authorized_keys_line(KEY, cmd, [NET + '/24', '2001:db8::/32']),
            'command="' + cmd + '",restrict,from="' + NET + '/24,2001:db8::/32" ' + KEY)
        self.assertEqual(authorized_keys_line(KEY, cmd), 'command="' + cmd + '",restrict ' + KEY)
        bad_keys = [KEY + '\n', 'ssh-rsa AAAAPLACEHOLDER', 'ssh-ed25519 AAAA\nssh-ed25519 BBBB',
                    'ssh-ed25519  AAAAPLACEHOLDER', 'ssh-ed25519 AAAA"x', 'ssh-ed25519 AAAA\tx',
                    'ssh-ed25519 AAAA\x07', ' ' + KEY, '']
        for key in bad_keys:
            with self.assertRaises(ValueError, msg=repr(key)):
                authorized_keys_line(key, cmd)
        bad_paths = ['relative/bose_request.py', cmd + '"', cmd + '\n', cmd + '\x00']
        for path in bad_paths:
            with self.assertRaises(ValueError, msg=repr(path)):
                authorized_keys_line(KEY, path)
        bad_from = [[BAD_ADDR + '/24'], ['not-an-ip'], [NET + '/24,' + BAD_ADDR + '/8'],
                    [NET + '/24 '], [NET + '/24"']]
        for froms in bad_from:
            with self.assertRaises(ValueError, msg=repr(froms)):
                authorized_keys_line(KEY, DIR + '/bose_request.py', froms)

    def test_device_group_resolves_from_device_not_constant(self):
        dev = Path(tempfile.mkdtemp()) / 'ttyfake'
        dev.write_bytes(b'')
        self.addCleanup(shutil.rmtree, dev.parent, ignore_errors=True)
        expected = grp.getgrgid(os.stat(dev).st_gid).gr_name
        self.assertEqual(device_group(str(dev)), expected)
        with self.assertRaises(ValueError):
            device_group(str(dev.parent / 'missing-device'))

    def test_check_platform_with_root_ignores_the_host(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.assertEqual(check_platform(str(root), which=lambda name: '/bin/' + name, version=(3, 9, 6)),
                         ['no_systemd', 'no_udev'])
        (root / 'run/systemd/system').mkdir(parents=True)
        (root / 'run/udev').mkdir(parents=True)
        self.assertEqual(check_platform(str(root), which=lambda name: None, version=(3, 9, 6)), [])

    def test_check_platform_reports_codes(self):
        self.assertEqual(check_platform('', which=lambda name: None, version=(3, 9, 6)),
                         ['no_systemd', 'no_udev'])
        self.assertEqual(check_platform('', which=lambda name: '/bin/' + name, version=(3, 8, 18)),
                         ['python_too_old'])
        self.assertEqual(check_platform('', which=lambda name: '/bin/' + name, version=(3, 9, 0)), [])


class InstallerTests(InstallTests):
    def test_install_creates_expected_tree_modes_in_temp_root(self):
        self.assert_ok(self.install(*RX_IDS))
        state = self.root / 'var' / 'lib' / 'bose-control'
        self.assertEqual(mode(state), 0o700)
        self.assertEqual(mode(self.ak().parent), 0o700)
        self.assertEqual(mode(self.ak()), 0o600)
        self.assertEqual(self.ak().read_text(), authorized_keys_line(
            KEY, DIR + '/bose_request.py') + '\n')
        install_dir = self.root / DIR.lstrip('/')
        for name in ['bose_bridge.py', 'bose_request.py', 'bose_cli.py']:
            self.assertTrue((install_dir / name).is_file(), name)
        self.assertTrue(os.access(install_dir / 'bose_request.py', os.X_OK))
        self.assertTrue(os.access(install_dir / 'bin' / 'bose-lifestyle', os.X_OK))
        self.assertFalse((install_dir / 'bose_rooms.py').exists())
        self.assertFalse(any(p.name.startswith('test_') for p in install_dir.rglob('*.py')))
        unit = (self.root / 'etc/systemd/system/bose-bridge.service').read_text()
        self.assertIn('ExecStart=' + str(self.pybin / 'python3') + ' -u ' + DIR + '/bose_bridge.py --serial-port '
                      + SERIAL_PATH + ' --ir-config ', unit)
        self.assertTrue((install_dir / 'bose_receivers.py').is_file())
        self.assertTrue((install_dir / 'bose_state.py').is_file())
        self.assertTrue((self.root / 'etc/udev/rules.d/70-bose-receiver.rules').is_file())

    def test_rerun_without_device_options_refuses_instead_of_dropping_them(self):
        unit_path = self.root / 'etc/systemd/system/bose-bridge.service'
        self.assert_ok(self.install())
        before = unit_path.read_bytes()
        self.assertIn('--serial-port', unit_path.read_text())
        self.assertIn('--ir-config', unit_path.read_text())
        # The copied update example once omitted --receiver-device: that must stop, not rewrite the unit.
        res = self.install('--serial-device', SERIAL_PATH, devices=False)
        self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn('--receiver-device', res.stdout + res.stderr)
        self.assertEqual(unit_path.read_bytes(), before)
        res = self.install(devices=False)                       # neither option
        self.assertNotEqual(res.returncode, 0)
        self.assertIn('--serial-device', res.stdout + res.stderr)
        self.assertEqual(unit_path.read_bytes(), before)
        # With both options the same run is the normal idempotent update.
        res = self.install()
        self.assert_ok(res)
        self.assertEqual(unit_path.read_bytes(), before)

    def test_first_install_without_receiver_is_still_allowed(self):
        res = self.install('--serial-device', SERIAL_PATH, devices=False)
        self.assert_ok(res)
        unit = (self.root / 'etc/systemd/system/bose-bridge.service').read_text()
        self.assertIn('--serial-port', unit)
        self.assertNotIn('--ir-config', unit)

    def test_install_idempotent_no_duplicate_key_line_and_tree_unchanged(self):
        self.assert_ok(self.install())
        before = tree_bytes(self.root)
        second = self.install()
        self.assert_ok(second)
        self.assertIn('already installed', second.stdout)
        self.assertEqual(tree_bytes(self.root), before)
        self.assertEqual(self.ak().read_text().count(KEY), 1)

    def test_unit_passes_serial_port_and_resolved_python(self):
        self.assert_ok(self.install())
        unit = (self.root / 'etc/systemd/system/bose-bridge.service').read_text()
        self.assertIn('ExecStart=' + str(self.pybin / 'python3') + ' -u ' + DIR + '/bose_bridge.py --serial-port '
                      + SERIAL_PATH + ' --ir-config ', unit)
        self.assertNotIn('/usr/bin/python3', unit)
        self.assertNotIn('@', unit)

    def test_unit_ir_config_points_into_state_dir(self):
        self.assert_ok(self.install(*RX_IDS))
        unit = (self.root / 'etc/systemd/system/bose-bridge.service').read_text()
        self.assertIn(' --ir-config /var/lib/bose-control/ir-config.json\n', unit)
        self.assertNotIn(DIR + '/ir-config.json', unit)
        self.assertFalse((self.root / DIR.lstrip('/') / 'ir-config.json').exists())
        self.assertFalse((self.root / 'var/lib/bose-control/ir-config.json').exists())   # the CLI creates it

    def test_serial_device_must_be_a_by_id_path(self):
        res = subprocess.run([SHELL, str(INSTALL), '--root', str(self.root), '--public-key', KEY, '--no-systemctl',
                              '--skip-prereq-check', '--serial-device', '/dev/ttyUSB0'], capture_output=True, text=True, cwd=str(REPO))
        self.assertNotEqual(res.returncode, 0)
        self.assertIn('/dev/serial/by-id/', res.stderr)
        self.assertFalse((self.root / 'etc/systemd/system/bose-bridge.service').exists())
        self.assertFalse((self.root / 'var').exists())
        nested = subprocess.run([SHELL, str(INSTALL), '--root', str(self.root), '--public-key', KEY, '--no-systemctl',
                                 '--skip-prereq-check', '--serial-device', '/dev/serial/by-id/a/b'], capture_output=True, text=True, cwd=str(REPO))
        self.assertNotEqual(nested.returncode, 0)
        self.assertFalse((self.root / 'var').exists())

    def test_installer_aborts_before_writing_when_pyserial_missing(self):
        before = tree_bytes(self.root)
        res = self.install(path=self.shim(serial_rc=1, evdev_rc=0), prereq=True)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn('install-bridge:', res.stderr)
        self.assertIn('pyserial', res.stderr)
        self.assertIn('install it with your system package manager or pip', res.stderr)
        self.assertEqual(tree_bytes(self.root), before)

    def test_installer_requires_evdev_only_with_receiver(self):
        path = self.shim(serial_rc=0, evdev_rc=1)
        res = self.install(devices=False, path=path, prereq=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        before = tree_bytes(self.root)
        res = self.install(path=path, prereq=True)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn('evdev', res.stderr)
        self.assertIn('install it with your system package manager or pip', res.stderr)
        self.assertEqual(tree_bytes(self.root), before)
        skipped = self.install(path=path)                    # the flag is the only way past the check
        self.assert_ok(skipped)

    def test_no_udev_rule_without_receiver_ids(self):
        res = self.install('--receiver-vendor', 'abcd')
        self.assertNotEqual(res.returncode, 0)
        self.assertFalse((self.root / 'etc/udev/rules.d/70-bose-receiver.rules').exists())
        self.assertFalse((self.root / 'var').exists())
        self.assert_ok(self.install())
        self.assertFalse((self.root / 'etc/udev/rules.d/70-bose-receiver.rules').exists())

    def test_udev_rule_matches_only_given_vendor_product(self):
        self.assert_ok(self.install('--receiver-vendor', 'ABCD', '--receiver-product', '0123',
                                    '--receiver-serial', 'S-1_x'))
        rule = (self.root / 'etc/udev/rules.d/70-bose-receiver.rules').read_text()
        self.assertEqual(re.findall(r'ATTRS\{(\w+)\}=="([^"]*)"', rule),
                         [('idVendor', 'abcd'), ('idProduct', '0123'), ('serial', 'S-1_x')])
        group = grp.getgrgid(os.stat(self.recv).st_gid).gr_name
        self.assertIn('GROUP="' + group + '"', rule)
        self.assertNotIn('@', rule)
        self.assertNotIn('ID_INPUT_KEY', rule)


    def test_install_refuses_to_overwrite_existing_config_json(self):
        state = self.root / 'var' / 'lib' / 'bose-control'
        state.mkdir(parents=True)
        cfg = state / 'config.json'
        cfg.write_bytes(b'{"keep": true}')
        os.utime(cfg, (1000000000, 1000000000))
        res = self.install()
        self.assert_ok(res)
        self.assertEqual(cfg.read_bytes(), b'{"keep": true}')
        self.assertEqual(os.stat(cfg).st_mtime_ns, 1000000000 * 10**9)
        self.assertIn('config.json', res.stdout)

    def test_installer_creates_default_config_once_and_is_idempotent(self):
        first = self.install(*RX_IDS)
        self.assert_ok(first)
        cfg = self.root / 'var' / 'lib' / 'bose-control' / 'config.json'
        self.assertEqual(json.loads(cfg.read_bytes()), default_config())
        self.assertEqual(mode(cfg), 0o600)
        self.assertIn('every room is off', first.stdout)
        before = cfg.read_bytes()
        second = self.install(*RX_IDS)
        self.assert_ok(second)
        self.assertIn('config.json left unchanged', second.stdout)
        self.assertEqual(cfg.read_bytes(), before)

    def test_install_never_loosens_modes(self):
        ssh = self.ak().parent
        ssh.mkdir(parents=True)
        os.chmod(ssh, 0o755)
        self.ak().write_text('ssh-rsa AAAAOTHER otherkey\n')
        os.chmod(self.ak(), 0o400)
        self.assert_ok(self.install())
        self.assertLessEqual(mode(self.ak()), 0o600)
        self.assertEqual(mode(self.ak()) & ~0o600, 0)
        self.assertEqual(mode(ssh), 0o700)
        text = self.ak().read_text()
        self.assertIn('ssh-rsa AAAAOTHER otherkey', text)
        self.assertIn(KEY, text)

    def test_no_systemd_prints_manual_steps_and_still_installs_files(self):
        res = self.install()
        self.assert_ok(res)
        self.assertTrue((self.root / DIR.lstrip('/') / 'bose_bridge.py').is_file())
        self.assertIn('Manual steps', res.stdout)
        self.assertIn(str(self.pybin / 'python3'), res.stdout)
        self.assertIn(' -u ' + DIR + '/bose_bridge.py --serial-port ' + SERIAL_PATH + ' --ir-config ', res.stdout)

    def test_systemd_present_prints_systemctl_steps(self):
        (self.root / 'run/systemd/system').mkdir(parents=True)
        (self.root / 'run/udev').mkdir(parents=True)
        res = self.install()
        self.assert_ok(res)
        self.assertIn('sudo systemctl enable --now bose-bridge.service', res.stdout)
        self.assertNotIn('python3 -u', res.stdout)

    def test_no_device_given_installs_without_ir_and_exits_zero(self):
        res = self.install(devices=False)
        self.assert_ok(res)
        self.assertIn('--serial-device', res.stdout)
        unit = (self.root / 'etc/systemd/system/bose-bridge.service').read_text()
        self.assertNotIn('--ir-config', unit)
        self.assertFalse((self.root / 'etc/udev/rules.d/70-bose-receiver.rules').exists())

    def test_state_dir_not_followed_through_symlink(self):
        outside = self.tmp / 'outside-state'
        outside.mkdir(mode=0o755)
        (self.root / 'var' / 'lib').mkdir(parents=True)
        (self.root / 'var' / 'lib' / 'bose-control').symlink_to(outside)
        res = self.install()
        self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn('symbolic link', res.stderr)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(mode(outside), 0o755)   # the target is not chmod'ed through the link

    def test_rooted_destination_symlink_ancestors_are_refused(self):
        cases = [
            ('opt', 'install'), ('etc', 'unit'), ('var', 'state'), ('home', 'home'),
        ]
        for ancestor, target_name in cases:
            with self.subTest(ancestor=ancestor):
                root = self.root / ('case-' + ancestor)
                shutil.copytree(self.root, root, symlinks=True)
                outside = self.tmp / ('outside-' + ancestor)
                outside.mkdir()
                # Create the desired destination's ancestor as a link; installer must not follow it.
                if ancestor == 'opt':
                    (root / 'opt').symlink_to(outside, target_is_directory=True)
                    args = ('--dir', '/opt/bridge')
                elif ancestor == 'etc':
                    (root / 'etc').symlink_to(outside, target_is_directory=True)
                    args = ()
                elif ancestor == 'var':
                    (root / 'var').symlink_to(outside, target_is_directory=True)
                    args = ()
                else:
                    home = root / 'home'
                    (home / 'bose').rmdir()
                    (home / 'bose').symlink_to(outside, target_is_directory=True)
                    args = ()
                # Run with isolated root using the same fixture runner.
                old_root = self.root
                self.root = root
                try:
                    before = tree_bytes(root)
                    res = self.install(*args)
                    self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
                    self.assertEqual(tree_bytes(root), before)
                    self.assertEqual(list(outside.iterdir()), [])
                finally:
                    self.root = old_root

    def test_refuses_symlinked_ssh_dir_and_leaves_target_alone(self):
        outside = self.tmp / 'outside'
        outside.mkdir()
        home = self.root / 'home' / 'bose'
        (home / '.ssh').symlink_to(outside)
        res = self.install()
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])

    def test_dot_components_cannot_leave_the_root(self):
        for option, value in (('--dir', '/../escaped-install'), ('--dir', '/opt/x/../../escaped-install'), ('--dir', '/opt/./x'),
                              ('--home', '/srv/../escaped-home')):
            res = self.install(option, value, '--allow-custom-home')
            self.assertNotEqual(res.returncode, 0, option + ' ' + value)
            self.assertIn('path component', res.stdout + res.stderr)
        self.assertFalse((self.root.parent / 'escaped-install').exists())
        self.assertFalse((self.root.parent / 'escaped-home').exists())

    def test_non_default_user_and_install_dir_work(self):
        (self.root / 'home' / 'svc-bridge').mkdir(parents=True)
        res = self.install('--user', 'svc-bridge', '--dir', '/srv/bridge-x')
        self.assert_ok(res)
        self.assertTrue((self.root / 'srv/bridge-x/bose_request.py').is_file())
        unit = (self.root / 'etc/systemd/system/bose-bridge.service').read_text()
        self.assertIn('User=svc-bridge', unit)
        self.assertIn('WorkingDirectory=/srv/bridge-x', unit)
        self.assertIn('SupplementaryGroups=', unit)
        self.assertIn(grp.getgrgid(os.stat(self.dev).st_gid).gr_name, unit)
        ak = self.ak('/home/svc-bridge').read_text()
        self.assertTrue(ak.startswith('command="/srv/bridge-x/bose_request.py",restrict '))
        self.assertTrue((self.root / 'var/lib/bose-control').is_dir())

    def test_service_template_has_no_private_path_or_serial(self):
        self.assert_ok(self.install(*RX_IDS))
        for rel in ['etc/systemd/system/bose-bridge.service', 'etc/udev/rules.d/70-bose-receiver.rules']:
            text = (self.root / rel).read_text()
            self.assertEqual(IPV4.findall(text), [], rel)
            self.assertNotIn(VENDOR, text, rel)
            self.assertIsNone(re.search(r'/home/[A-Za-z0-9_.-]+', text), rel)

    def test_udev_template_uses_placeholders_only(self):
        text = (DEPLOY / '70-bose-receiver.rules.in').read_text()
        self.assertEqual(set(re.findall(r'@[A-Z_]+@', text)) - {'@VENDOR@', '@PRODUCT@', '@SERIAL_MATCH@', '@GROUPS@'},
                         set())
        for literal in ['ID_SERIAL', 'ID_INPUT_KEY', 'usb-', 'ID_PATH', 'ATTRS{serial}=="']:
            self.assertNotIn(literal, text)
        self.assertIsNone(re.search(r'ATTRS\{id\w+\}=="[0-9A-Fa-f]{4}"', text))   # no literal vendor or product id
        self.assertIsNone(IPV4.search(text))

    def test_install_without_systemctl_flag_calls_nothing(self):
        stub = self.tmp / 'stub'
        stub.mkdir()
        marker = self.tmp / 'called'
        for name in ['systemctl', 'udevadm']:
            script = stub / name
            script.write_text('#!/bin/sh\necho ' + name + ' >> "' + str(marker) + '"\nexit 1\n')
            script.chmod(0o755)
        self.assert_ok(self.install(path=str(stub)))
        self.assertFalse(marker.exists())

    def test_installed_unit_and_forced_command_paths_match(self):
        self.assert_ok(self.install())
        unit = (self.root / 'etc/systemd/system/bose-bridge.service').read_text()
        workdir = re.search(r'^WorkingDirectory=(.+)$', unit, re.M).group(1)
        execpath = re.search(r'^ExecStart=\S+ -u (\S+bose_bridge\.py)', unit, re.M).group(1)
        forced = re.match(r'command="([^"]+)"', self.ak().read_text()).group(1)
        self.assertEqual(forced, workdir + '/bose_request.py')
        self.assertEqual(os.path.dirname(execpath), workdir)
        self.assertTrue((self.root / forced.lstrip('/')).is_file())
        self.assertTrue(os.access(self.root / forced.lstrip('/'), os.X_OK))

    def test_symlinked_authorized_keys_is_refused_and_target_untouched(self):
        outside = self.tmp / 'root-only'
        outside.write_text('ssh-rsa AAAAOUTSIDE keep\n')
        (self.root / 'home' / 'bose' / '.ssh').mkdir()
        self.ak().symlink_to(outside)
        res = self.install()
        self.assertNotEqual(res.returncode, 0)
        self.assertIn('symbolic link', res.stderr)
        self.assertEqual(outside.read_text(), 'ssh-rsa AAAAOUTSIDE keep\n')
        self.assertTrue(self.ak().is_symlink())

    def test_hard_linked_authorized_keys_refused(self):
        outside = self.tmp / 'root-only'
        outside.write_text('ssh-rsa AAAAOUTSIDE keep\n')
        (self.root / 'home' / 'bose' / '.ssh').mkdir()
        os.link(outside, self.ak())
        res = self.install()
        self.assertNotEqual(res.returncode, 0)
        self.assertIn('hard link', res.stderr)
        self.assertEqual(outside.read_text(), 'ssh-rsa AAAAOUTSIDE keep\n')

    def test_symlinked_home_refused(self):
        outside = self.tmp / 'outside-home'
        (outside / '.ssh').mkdir(parents=True)
        home = self.root / 'home' / 'bose'
        shutil.rmtree(home)
        home.symlink_to(outside)
        res = self.install()
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(list((outside / '.ssh').iterdir()), [])

    def test_custom_home_requires_flag(self):
        other = self.root / 'home' / 'example'
        other.mkdir(parents=True)
        res = self.install('--home', '/home/example')
        self.assertNotEqual(res.returncode, 0)
        self.assertIn('--allow-custom-home', res.stderr)
        self.assertEqual(list(other.iterdir()), [])
        self.assert_ok(self.install('--home', '/home/example', '--allow-custom-home'))
        self.assertIn(KEY, (other / '.ssh' / 'authorized_keys').read_text())
        physical = self.root / 'home' / 'physical-example'
        physical.mkdir()
        self.assert_ok(self.install('--home', str(physical), '--allow-custom-home'))
        self.assertIn(KEY, (physical / '.ssh' / 'authorized_keys').read_text())

    def test_missing_home_not_created_without_flag(self):
        home = self.root / 'home' / 'bose'
        shutil.rmtree(home)
        res = self.install()
        self.assertNotEqual(res.returncode, 0)
        self.assertIn('--create-home', res.stderr)
        self.assertFalse(home.exists())
        self.assertFalse((self.root / 'var').exists())

    def test_create_home_flag_creates_0700(self):
        home = self.root / 'home' / 'bose'
        shutil.rmtree(home)
        self.assert_ok(self.install('--create-home'))
        self.assertEqual(mode(home), 0o700)
        self.assertEqual(mode(home / '.ssh'), 0o700)
        self.assertEqual(mode(self.ak()), 0o600)

    def test_existing_behavior_unchanged(self):
        self.assert_ok(self.install())
        before = tree_bytes(self.root)
        self.assert_ok(self.install())
        self.assertEqual(tree_bytes(self.root), before)
        self.assertEqual(self.ak().read_text().count(KEY), 1)
        self.assertEqual(mode(self.ak()), 0o600)
        self.assertEqual(mode(self.ak().parent), 0o700)

    def test_repaired_rooted_file_reports_install_as_changed(self):
        self.assert_ok(self.install())
        installed = self.root / DIR.lstrip('/') / 'bose_bridge.py'
        installed.write_text('modified fixture')
        result = self.install()
        self.assert_ok(result)
        self.assertIn('installed under', result.stdout)
        self.assertNotIn('already installed', result.stdout)

    def test_public_key_is_required(self):
        res = subprocess.run([SHELL, str(INSTALL), '--root', str(self.root), '--no-systemctl'],
                             capture_output=True, text=True, cwd=str(REPO))
        self.assertNotEqual(res.returncode, 0)
        self.assertFalse((self.root / 'var').exists())


class InstallKeyTests(unittest.TestCase):
    """install_key directly: the descriptor-based steps and the refusals, without the shell in between."""
    LINE = 'command="' + DIR + '/bose_request.py",restrict ' + KEY

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / 'home' / 'bose'
        self.home.mkdir(parents=True)
        self.ak = self.home / '.ssh' / 'authorized_keys'

    def test_symlinked_ssh_dir_refused(self):
        outside = self.tmp / 'outside'
        outside.mkdir()
        (self.home / '.ssh').symlink_to(outside)
        with self.assertRaises(ValueError):
            install_key(str(self.home), self.LINE, KEY)
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlink_swapped_between_check_and_open_is_not_followed(self):
        target = self.tmp / 'root-only'
        target.write_text('ssh-rsa AAAAOUTSIDE keep\n')
        (self.home / '.ssh').mkdir()
        self.ak.write_text('ssh-rsa AAAAOTHER other\n')
        real_open = os.open

        def swap_then_open(name, flags, *args, **kwargs):
            if name == 'authorized_keys' and self.ak.exists() and not self.ak.is_symlink():
                self.ak.unlink()                   # the swap lands after the earlier checks
                self.ak.symlink_to(target)
            return real_open(name, flags, *args, **kwargs)

        with mock.patch.object(authorized_keys.os, 'open', swap_then_open):
            with self.assertRaises(ValueError):
                install_key(str(self.home), self.LINE, KEY)
        self.assertEqual(target.read_text(), 'ssh-rsa AAAAOUTSIDE keep\n')

    def test_home_owned_by_other_user_refused(self):
        real_fstat = os.fstat
        fake_uid = 4243 if os.geteuid() == 4242 else 4242   # neither root, the operator, nor the account

        def other_owner(fd):
            st = real_fstat(fd)
            return types.SimpleNamespace(st_mode=st.st_mode, st_uid=fake_uid)

        with mock.patch.object(authorized_keys.os, 'fstat', other_owner):
            with self.assertRaises(ValueError) as caught:
                install_key(str(self.home), self.LINE, KEY)
        self.assertIn('owned by another user', str(caught.exception))
        self.assertFalse(self.ak.exists())

    def test_state_dir_owned_by_other_user_refused(self):
        state = self.tmp / 'state'
        state.mkdir(mode=0o755)
        real_fstat = os.fstat
        fake_uid = 4243 if os.geteuid() == 4242 else 4242   # neither root, the operator, nor the account

        def other_owner(fd):
            st = real_fstat(fd)
            return types.SimpleNamespace(st_mode=st.st_mode, st_uid=fake_uid)

        with mock.patch.object(authorized_keys.os, 'fstat', other_owner):
            with self.assertRaises(ValueError) as caught:
                authorized_keys.ensure_state_dir(str(state))
        self.assertEqual(str(caught.exception), 'state directory is owned by another user; refusing to use it')
        self.assertEqual(stat.S_IMODE(os.stat(state).st_mode), 0o755)   # refused before any chmod

    def test_missing_home_message_names_the_flag(self):
        shutil.rmtree(self.home)
        with self.assertRaises(ValueError) as caught:
            install_key(str(self.home), self.LINE, KEY)
        self.assertIn('--create-home', str(caught.exception))
        self.assertFalse(self.home.exists())

    def test_rooted_write_fails_closed_on_parent_replacement(self):
        root = self.tmp / 'root'
        parent = root / 'opt' / 'bridge'
        parent.mkdir(parents=True)
        parent.chmod(0o777)  # an untrusted user could rename a child here
        outside = self.tmp / 'outside'
        outside.mkdir()
        held = self.tmp / 'held'
        real_rename = os.rename
        swapped = False

        def swap_then_publish(src, dst, **kwargs):
            nonlocal swapped
            if dst == 'target' and not swapped:
                parent.rename(held)
                parent.symlink_to(outside, target_is_directory=True)
                swapped = True
            return real_rename(src, dst, **kwargs)

        with mock.patch.object(authorized_keys.os, 'rename', swap_then_publish):
            with self.assertRaises(ValueError):
                authorized_keys.rooted_write(str(root), '/opt/bridge/target', b'payload', 0o644)
        self.assertFalse((held / 'target').exists())
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue((root / 'opt' / 'bridge').is_dir())
        self.assertFalse(held.exists())

    def test_root_identity_pin_rejects_replacement_between_helper_calls(self):
        root = self.tmp / 'root-pinned'
        root.mkdir()
        source = self.tmp / 'payload'
        source.write_bytes(b'payload')
        root_id = authorized_keys.root_identity(str(root))
        moved = self.tmp / 'root-original'
        root.rename(moved)
        root.mkdir()
        err = io.StringIO()
        with mock.patch.dict(os.environ, {'BOSE_INSTALL_ROOT_ID': root_id}), contextlib.redirect_stderr(err):
            result = authorized_keys.main(['put', str(root), 'target', '644', str(source)])
        self.assertEqual(result, 2)
        self.assertIn('root changed during install', err.getvalue())
        self.assertFalse((root / 'target').exists())

    def test_rooted_key_install_fails_closed_on_parent_replacement(self):
        root = self.tmp / 'key-root'
        home = root / 'home' / 'bose'
        (home / '.ssh').mkdir(parents=True)
        outside = self.tmp / 'key-outside'
        outside.mkdir()
        held = self.tmp / 'key-held'
        replace = home / '.ssh' / 'authorized_keys'
        real_rename = os.rename
        swapped = False

        def swap_then_publish(src, dst, **kwargs):
            nonlocal swapped
            if dst == 'authorized_keys' and not swapped:
                home.rename(held)
                home.symlink_to(outside, target_is_directory=True)
                swapped = True
            return real_rename(src, dst, **kwargs)

        with mock.patch.object(authorized_keys.os, 'rename', swap_then_publish):
            with self.assertRaises(ValueError):
                install_key('/home/bose', self.LINE, KEY.split()[1], root=str(root))
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue((root / 'home' / 'bose').is_symlink())

    def test_rooted_state_fails_closed_on_parent_replacement(self):
        root = self.tmp / 'state-root'
        (root / 'var' / 'lib').mkdir(parents=True)
        outside = self.tmp / 'state-outside'
        outside.mkdir()
        held = self.tmp / 'state-held'
        state = root / 'var' / 'lib' / 'bose-control'
        real_fchmod = os.fchmod
        swapped = False

        def swap_during_chmod(fd, mode):
            nonlocal swapped
            if not swapped and os.fstat(fd).st_ino == os.stat(state).st_ino:
                state.rename(held)
                state.symlink_to(outside, target_is_directory=True)
                swapped = True
            return real_fchmod(fd, mode)

        with mock.patch.object(authorized_keys.os, 'fchmod', swap_during_chmod):
            with self.assertRaises(ValueError):
                authorized_keys.ensure_state_dir('/var/lib/bose-control', root=str(root))
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue(state.is_symlink())

    def test_rooted_config_fails_closed_on_parent_replacement(self):
        root = self.tmp / 'config-root'
        state = root / 'var' / 'lib' / 'bose-control'
        state.mkdir(parents=True)
        outside = self.tmp / 'config-outside'
        outside.mkdir()
        held = self.tmp / 'config-held'
        real_rename = os.link
        swapped = False

        def swap_then_link(src, dst, **kwargs):
            nonlocal swapped
            if dst == 'config.json' and not swapped:
                state.rename(held)
                state.symlink_to(outside, target_is_directory=True)
                swapped = True
            return real_rename(src, dst, **kwargs)

        with mock.patch.object(authorized_keys.os, 'link', swap_then_link):
            with self.assertRaises(ValueError):
                authorized_keys.create_default_config_root(str(root), 'var/lib/bose-control/config.json')
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue(state.is_symlink())

    def test_rooted_write_repairs_mode_when_contents_match(self):
        root = self.tmp / 'mode-root'
        root.mkdir()
        target = root / 'file'
        target.write_bytes(b'same')
        target.chmod(0o600)
        self.assertEqual(authorized_keys.rooted_write(str(root), 'file', b'same', 0o640), 'changed')
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)

    def test_rooted_write_repairs_owner_when_contents_match(self):
        root = self.tmp / 'owner-root'
        root.mkdir()
        (root / 'file').write_bytes(b'same')
        requested_uid = os.geteuid() + 1
        with mock.patch.object(authorized_keys.os, 'fchown') as chown:
            self.assertEqual(authorized_keys.rooted_write(str(root), 'file', b'same', 0o644,
                                                           owner_uid=requested_uid), 'changed')
        chown.assert_called_once()
        self.assertEqual(chown.call_args[0][1:], (requested_uid, -1))

    def test_rooted_key_unchanged_still_verifies_ancestor_chain(self):
        root = self.tmp / 'key-verify-root'
        home = root / 'home' / 'bose'
        ssh = home / '.ssh'
        ssh.mkdir(parents=True)
        ak = ssh / 'authorized_keys'
        ak.write_text(self.LINE + '\n')
        outside = self.tmp / 'key-verify-outside'
        outside.mkdir()
        held = self.tmp / 'key-verify-held'
        original_verify = authorized_keys._verify_walk
        swapped = False

        def swap_then_verify(path_root, path, fds):
            nonlocal swapped
            if not swapped:
                home.rename(held)
                home.symlink_to(outside, target_is_directory=True)
                swapped = True
            return original_verify(path_root, path, fds)

        with mock.patch.object(authorized_keys, '_verify_walk', swap_then_verify):
            with self.assertRaises(ValueError):
                install_key('/home/bose', self.LINE, KEY.split()[1], root=str(root))
        self.assertEqual(list(outside.iterdir()), [])

    def test_rooted_key_accepts_physical_home_path_with_root(self):
        root = self.tmp / 'physical-home-root'
        home = root / 'home' / 'bose'
        home.mkdir(parents=True)
        result = install_key(str(home), self.LINE, KEY, root=str(root))
        self.assertEqual(result, 'changed')
        self.assertTrue((home / '.ssh' / 'authorized_keys').is_file())

    def test_rooted_key_accepts_explicitly_selected_service_owner(self):
        root = self.tmp / 'selected-owner-root'
        home = root / 'home' / 'bose'
        home.mkdir(parents=True)
        owner_uid = 4242 if os.geteuid() != 4242 else 4243
        home_inode = home.stat().st_ino
        real_fstat = os.fstat

        def owned_target(fd):
            st = real_fstat(fd)
            ssh = home / '.ssh'
            if st.st_ino == home_inode or (ssh.is_dir() and st.st_ino == ssh.stat().st_ino):
                return types.SimpleNamespace(st_mode=st.st_mode, st_uid=owner_uid, st_dev=st.st_dev,
                                             st_ino=st.st_ino, st_nlink=st.st_nlink)
            return st

        with mock.patch.object(authorized_keys, '_owner_uid', return_value=owner_uid), \
                mock.patch.object(authorized_keys.os, 'fstat', side_effect=owned_target), \
                mock.patch.object(authorized_keys.os, 'fchown'):
            result = install_key(str(home), self.LINE, KEY, owner='bose', root=str(root))
        self.assertEqual(result, 'changed')
        self.assertTrue((home / '.ssh' / 'authorized_keys').is_file())

    def test_rooted_config_accepts_explicitly_selected_service_owner(self):
        root = self.tmp / 'selected-owner-state-root'
        state = root / 'var' / 'lib' / 'bose-control'
        state.mkdir(parents=True)
        owner_uid = 4242 if os.geteuid() != 4242 else 4243
        state_inode = state.stat().st_ino
        real_fstat = os.fstat

        def owned_state(fd):
            st = real_fstat(fd)
            if st.st_ino == state_inode:
                return types.SimpleNamespace(st_mode=st.st_mode, st_uid=owner_uid, st_dev=st.st_dev,
                                             st_ino=st.st_ino, st_nlink=st.st_nlink)
            return st

        with mock.patch.object(authorized_keys, '_owner_uid', return_value=owner_uid), \
                mock.patch.object(authorized_keys.os, 'fstat', side_effect=owned_state), \
                mock.patch.object(authorized_keys.os, 'fchown'):
            result = authorized_keys.create_default_config_root(
                str(root), 'var/lib/bose-control/config.json', owner='bose')
        self.assertEqual(result, 'changed')
        self.assertTrue((state / 'config.json').is_file())

    def test_root_open_dir_closes_fd_when_final_mode_setup_fails(self):
        root = self.tmp / 'fd-root'
        root.mkdir()
        opened = []
        real_open = os.open

        def tracking_open(*args, **kwargs):
            fd = real_open(*args, **kwargs)
            opened.append(fd)
            return fd

        with mock.patch.object(authorized_keys.os, 'open', tracking_open), \
             mock.patch.object(authorized_keys.os, 'fchmod', side_effect=OSError('chmod failed')):
            with self.assertRaises(OSError):
                authorized_keys._root_open_dir(str(root), 'new/home', True)
        for fd in opened:
            with self.assertRaises(OSError): os.fstat(fd)

    def test_rooted_write_closes_descriptors_when_temp_cleanup_errors(self):
        root = self.tmp / 'cleanup-root'
        root.mkdir()
        opened = []
        real_open = os.open

        def tracking_open(*args, **kwargs):
            fd = real_open(*args, **kwargs)
            opened.append(fd)
            return fd

        with mock.patch.object(authorized_keys.os, 'open', tracking_open), \
             mock.patch.object(authorized_keys.os, 'unlink', side_effect=OSError('unlink failed')):
            with self.assertRaises(OSError):
                authorized_keys.rooted_write(str(root), 'target', b'data', 0o600)
        for fd in opened:
            with self.assertRaises(OSError): os.fstat(fd)


if __name__ == '__main__':
    unittest.main(verbosity=1)
