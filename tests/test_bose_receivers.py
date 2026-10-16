"""Receiver selection: by-id listing, key verification, exact ir-config.json, udev rule, CLI ir commands.

Fake devices and fake openers only; evdev is never imported. Run: python3 -B tests/test_bose_receivers.py
"""
import _bootstrap
import io
import json
import os
import re
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import bose_receivers as br  # noqa: E402
from bose_bridge import load_ir_config  # noqa: E402
import bose_cli  # noqa: E402

BY_ID_RECEIVER = '/dev/input/by-id/usb-example-receiver-event-kbd'
KEY_CODES = {'volume-up': 115, 'volume-down': 114, 'mute': 113}
# Built at runtime so this file holds no vendor literal; the banned board-name word is checked as a whole word.
FORBIDDEN = ['FT' + 'DI', 'Rasp' + 'berry']


class FakeEvent:
    def __init__(self, type_, code, value):
        self.type, self.code, self.value = type_, code, value


class FakeDevice:
    """Same shape as evdev.InputDevice for the parts used here. `fd` is readable while events are queued."""

    def __init__(self, name, keys=True, batches=(), fail_after=None):
        self.name = name
        self.keys = keys
        self.batches = list(batches)
        self.fail_after = fail_after   # raise OSError on the read after this many successful reads
        self.reads = 0
        self.closed = False
        self.read_fd, self.write_fd = os.pipe()
        os.write(self.write_fd, b'x')

    @property
    def fd(self):
        return self.read_fd

    def capabilities(self):
        return {br.EV_KEY: [1]} if self.keys else {}

    def read(self):
        if self.fail_after is not None and self.reads >= self.fail_after:
            raise OSError(19, 'No such device')
        self.reads += 1
        return self.batches.pop(0) if self.batches else []

    def close(self):
        self.closed = True
        for fd in (self.read_fd, self.write_fd):
            os.close(fd)


class FakeClock:
    def __init__(self, step=0.5):
        self.now, self.step = 0.0, step

    def __call__(self):
        self.now += self.step
        return self.now


def press(code):
    return FakeEvent(br.EV_KEY, code, 1)


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.input = self.tmp / 'input'
        self.by_id = self.tmp / 'by-id'
        self.input.mkdir()
        self.by_id.mkdir()

    def node(self, name):
        path = self.input / name
        path.write_bytes(b'')
        return path

    def link(self, name, target):
        (self.by_id / name).symlink_to(target)
        return self.by_id / name

    def opener_for(self, devices):
        def opener(path):
            # Keys may be the by-id link name or the eventN node it resolves to; a fresh device per open.
            for key in (os.path.basename(path), os.path.basename(os.path.realpath(path))):
                if key in devices:
                    return devices[key]()
            raise OSError(2, 'missing')
        return opener

    def test_list_includes_only_by_id_key_devices(self):
        self.node('event3')
        self.node('event4')
        self.node('mouse0')
        rx = self.link('usb-example-receiver-event-kbd', str(self.input / 'event3'))
        self.link('usb-example-mouse-event-mouse', str(self.input / 'event4'))
        self.link('usb-example-mouse-mouse', str(self.input / 'mouse0'))          # legacy node, not an event device
        self.link('usb-example-escape-event-kbd', '/etc/hosts')                  # resolves outside /dev/input
        (self.by_id / 'plain-file').write_text('not a link')
        self.link('usb-example-dangling-event-kbd', str(self.input / 'event9'))  # cannot be opened
        devices = {'event3': lambda: FakeDevice('Test receiver'),
                   'event4': lambda: FakeDevice('Test mouse', keys=False)}
        found = br.list_receivers(str(self.by_id), self.opener_for(devices), str(self.input))
        self.assertEqual(found, [br.Receiver(str(rx), 'Test receiver', True)])

    def test_device_without_by_id_is_reported_unsupported(self):
        self.node('event3')
        self.node('event7')
        self.node('event8')
        self.link('usb-example-receiver-event-kbd', str(self.input / 'event3'))
        devices = {'event3': lambda: FakeDevice('Covered'), 'event7': lambda: FakeDevice('Bare receiver'),
                   'event8': lambda: FakeDevice('Power button', keys=False)}
        opener = self.opener_for(devices)
        self.assertEqual(br.list_unsupported(str(self.input), str(self.by_id), opener),
                         [br.Receiver(str(self.input / 'event7'), 'Bare receiver', True)])
        self.assertEqual([r.name for r in br.list_receivers(str(self.by_id), opener, str(self.input))], ['Covered'])

    def test_verify_returns_codes_and_times_out_with_plain_message(self):
        dev = FakeDevice('Test receiver', batches=[[press(115), FakeEvent(br.EV_KEY, 115, 2), press(114)]])
        self.assertEqual(br.verify_receiver('/dev/input/by-id/x', timeout=3, opener=lambda p: dev,
                                            clock=FakeClock()), [115, 114])
        self.assertTrue(dev.closed)
        silent = FakeDevice('Silent receiver')
        self.assertEqual(br.verify_receiver('/dev/input/by-id/x', timeout=3, opener=lambda p: silent,
                                            clock=FakeClock()), [])
        busy = FakeDevice('Busy receiver', batches=[[press(100 + i) for i in range(40)]])
        codes = br.verify_receiver('/dev/input/by-id/x', timeout=3, opener=lambda p: busy, clock=FakeClock())
        self.assertEqual(len(codes), br.MAX_CODES)

    def test_unplug_mid_verify_leaves_ir_config_unchanged(self):
        config = self.tmp / 'ir-config.json'
        receiver = br.Receiver(BY_ID_RECEIVER, 'Test receiver', True)
        br.write_ir_config(config, receiver, KEY_CODES)
        before = config.read_bytes()
        gone = FakeDevice('Test receiver', batches=[[press(115)]], fail_after=1)
        with self.assertRaises(br.ReceiverGone) as caught:
            br.verify_receiver(receiver.path, timeout=3, opener=lambda p: gone, clock=FakeClock())
        self.assertEqual(caught.exception.codes, [115])
        self.assertTrue(gone.closed)
        out, err = io.StringIO(), io.StringIO()
        code = bose_cli.main(['ir', 'verify', receiver.path], socket_path=str(self.tmp / 'no.sock'),
                             state_dir=str(self.tmp), out=out, err=err, ir_config=str(config),
                             by_id_dir=str(self.by_id), input_dir=str(self.input),
                             opener=lambda p: FakeDevice('Test receiver', batches=[[press(115)]], fail_after=1),
                             clock=FakeClock())
        self.assertEqual(code, 1)
        self.assertIn('unplugged', err.getvalue())
        self.assertEqual(config.read_bytes(), before)

    def test_write_ir_config_matches_bridge_schema(self):
        config = self.tmp / 'ir-config.json'
        receiver = br.Receiver(BY_ID_RECEIVER, 'Test receiver', True)
        br.write_ir_config(config, receiver, KEY_CODES)
        self.assertEqual(stat.S_IMODE(os.stat(config).st_mode), 0o600)
        self.assertEqual(json.loads(config.read_text()), {
            'ir_device': receiver.path, 'ir_name': 'Test receiver', 'ir_keys': [115, 114, 113]})
        self.assertEqual(load_ir_config(str(config))['ir_name'], 'Test receiver')   # the bridge's own loader
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ['by-id', 'input', 'ir-config.json'])
        before = config.read_bytes()
        bad = [
            (br.Receiver('/dev/input/event3', 'Test receiver', True), KEY_CODES),
            (br.Receiver(BY_ID_RECEIVER, '', True), KEY_CODES),
            (br.Receiver(BY_ID_RECEIVER, 'x' * 256, True), KEY_CODES),
            (receiver, {'volume-up': 115, 'volume-down': 114}),
            (receiver, dict(KEY_CODES, mute=True)),
            (receiver, dict(KEY_CODES, mute=-1)),
        ]
        for rx, keys in bad:
            with self.assertRaises(ValueError, msg=repr(rx)):
                br.write_ir_config(config, rx, keys)
        self.assertEqual(config.read_bytes(), before)

    def test_udev_rule_contains_only_given_ids_and_rejects_bad_input(self):
        rule = br.render_udev_rule('ABCD', '0123')
        ids = re.findall(r'ATTRS\{(\w+)\}=="([^"]*)"', rule)
        self.assertEqual(ids, [('idVendor', 'abcd'), ('idProduct', '0123')])
        self.assertIn('GROUP="@GROUPS@"', rule)
        self.assertNotIn('ATTRS{serial}', rule)
        serial_rule = br.render_udev_rule('abcd', '0123', 'S-1_x')
        self.assertEqual(re.findall(r'ATTRS\{(\w+)\}=="([^"]*)"', serial_rule)[-1], ('serial', 'S-1_x'))
        template = br.TEMPLATE.read_text()
        self.assertEqual(set(re.findall(r'@[A-Z_]+@', template)), {'@VENDOR@', '@PRODUCT@', '@SERIAL_MATCH@', '@GROUPS@'})
        self.assertNotIn('ID_INPUT_KEY', template)
        for bad in [('abc', '0123'), ('ab cd', '0123'), ('0x12ab', '0123'), ('abcd', '0123\n'), ('abcd', ''),
                    ('abcd', '0123', ''), ('abcd', '0123', 'S"1'), ('abcd', '0123', 'S\n1'), (1234, '0123')]:
            with self.assertRaises(ValueError, msg=repr(bad)):
                br.render_udev_rule(*bad)

    def test_no_vendor_or_brand_literal_in_module(self):
        texts = [(ROOT / 'bose_receivers.py').read_text(), br.TEMPLATE.read_text()]
        for text in texts:
            for word in FORBIDDEN:
                self.assertNotIn(word, text)
            self.assertIsNone(re.search(r'\bPi\b', text))

    def test_cli_ir_commands_never_transmit(self):
        config = self.tmp / 'ir-config.json'
        receiver_path = BY_ID_RECEIVER
        self.link('usb-example-receiver-event-kbd', str(self.input / 'event3'))
        self.node('event3')
        devices = {'event3': lambda: FakeDevice('Test receiver', batches=[[press(115)]]),
                   'usb-example-receiver-event-kbd': lambda: FakeDevice('Test receiver', batches=[[press(115)]])}
        opener = self.opener_for(devices)
        common = dict(socket_path=str(self.tmp / 'no.sock'), state_dir=str(self.tmp), ir_config=str(config),
                      by_id_dir=str(self.by_id), input_dir=str(self.input), opener=opener, clock=FakeClock())
        def only_status(path, action=b''):      # ir list may ask the bridge for status (not running here), nothing else
            if action == b'status\n':
                raise FileNotFoundError(2, 'no bridge')
            raise AssertionError('request used: ' + repr(action))
        with patch('bose_request.request', side_effect=only_status):
            out, err = io.StringIO(), io.StringIO()
            self.assertEqual(bose_cli.main(['ir', 'list'], out=out, err=err, **common), 0)
            self.assertIn('usb-example-receiver-event-kbd  Test receiver', out.getvalue())
            out, err = io.StringIO(), io.StringIO()
            self.assertEqual(bose_cli.main(['ir', 'verify', receiver_path], out=out, err=err, **common), 0)
            self.assertIn('115', out.getvalue())
            out, err = io.StringIO(), io.StringIO()
            self.assertEqual(bose_cli.main(['ir', 'select', receiver_path, '--volume-up', '115',
                                            '--volume-down', '114', '--mute', '113'], out=out, err=err, **common), 0)
            self.assertEqual(load_ir_config(str(config))['ir_keys'], [115, 114, 113])
        self.assertNotIn('evdev', sys.modules)

    def test_ir_select_writes_into_state_dir_not_install_dir(self):
        state = self.tmp / 'state'
        state.mkdir()
        self.node('event3')
        self.link('usb-example-receiver-event-kbd', str(self.input / 'event3'))
        install_default = ROOT / 'ir-config.json'   # the old default; a gitignored copy may exist, so compare bytes
        before = install_default.read_bytes() if install_default.exists() else None
        out, err = io.StringIO(), io.StringIO()
        code = bose_cli.main(['ir', 'select', BY_ID_RECEIVER, '--volume-up', '115', '--volume-down', '114',
                              '--mute', '113'], socket_path=str(self.tmp / 'no.sock'), state_dir=str(state),
                             by_id_dir=str(self.by_id), input_dir=str(self.input),
                             opener=self.opener_for({'event3': lambda: FakeDevice('Test receiver'),
                                                     'usb-example-receiver-event-kbd': lambda: FakeDevice('Test receiver')}),
                             out=out, err=err)
        self.assertEqual(code, 0, err.getvalue())
        saved = state / 'ir-config.json'
        self.assertEqual(load_ir_config(str(saved))['ir_keys'], [115, 114, 113])
        self.assertEqual(stat.S_IMODE(os.stat(saved).st_mode), 0o600)
        self.assertEqual([p.name for p in state.iterdir()], ['ir-config.json'])
        self.assertIn(str(saved), out.getvalue())
        self.assertIn('Next: learn your remote with `bose-lifestyle ir learn --name NAME`', out.getvalue())
        after = install_default.read_bytes() if install_default.exists() else None
        self.assertEqual(after, before)

    def test_cli_select_in_unwritable_directory_says_so(self):
        if os.geteuid() == 0:
            self.skipTest('root can write anywhere')
        locked = self.tmp / 'locked'
        locked.mkdir()
        config = locked / 'ir-config.json'
        os.chmod(locked, 0o500)
        self.addCleanup(os.chmod, locked, 0o700)
        self.node('event3')
        self.link('usb-example-receiver-event-kbd', str(self.input / 'event3'))
        out, err = io.StringIO(), io.StringIO()
        code = bose_cli.main(['ir', 'select', BY_ID_RECEIVER, '--volume-up', '115', '--volume-down', '114',
                              '--mute', '113'], socket_path=str(self.tmp / 'no.sock'), state_dir=str(self.tmp),
                             ir_config=str(config), by_id_dir=str(self.by_id), input_dir=str(self.input),
                             opener=self.opener_for({'event3': lambda: FakeDevice('Test receiver'),
                                                     'usb-example-receiver-event-kbd': lambda: FakeDevice('Test receiver')}),
                             out=out, err=err)
        self.assertEqual(code, 1)
        self.assertIn('cannot save', err.getvalue())
        self.assertFalse(config.exists())

    def test_cli_no_keys_message_and_unsupported_listing(self):
        self.node('event3')
        self.node('event7')
        self.link('usb-example-receiver-event-kbd', str(self.input / 'event3'))
        devices = {'event3': lambda: FakeDevice('Silent'), 'event7': lambda: FakeDevice('Bare receiver'),
                   'usb-example-receiver-event-kbd': lambda: FakeDevice('Silent')}
        common = dict(socket_path=str(self.tmp / 'no.sock'), state_dir=str(self.tmp),
                      ir_config=str(self.tmp / 'ir-config.json'), by_id_dir=str(self.by_id),
                      input_dir=str(self.input), opener=self.opener_for(devices), clock=FakeClock())
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(bose_cli.main(['ir', 'verify', BY_ID_RECEIVER],
                                       out=out, err=err, **common), 1)
        self.assertIn('no key presses seen in 15 seconds', err.getvalue())
        out, err = io.StringIO(), io.StringIO()
        bose_cli.main(['ir', 'list'], out=out, err=err, **common)
        self.assertIn('Unsupported', out.getvalue())
        self.assertIn('no stable /dev/input/by-id/ path', out.getvalue())
        self.assertFalse((self.tmp / 'ir-config.json').exists())


    def test_ir_list_shows_learned_remotes_without_codes(self):
        self.node('event3')
        self.link('usb-example-receiver-event-kbd', str(self.input / 'event3'))
        rid = 'a' * 32
        status = {'ir': {'profiles': {rid: {'name': 'Den remote', 'model': 'ir-remote', 'active': True, 'mappings': {
            'volume-up': {'code': 70045, 'enabled': True}, 'volume-down': {'code': None, 'enabled': False},
            'mute': {'code': 70047, 'enabled': False}}}}}}
        out, err = io.StringIO(), io.StringIO()
        with patch.object(bose_cli.SocketBackend, 'status', return_value=status):
            code = bose_cli.main(['ir', 'list'], socket_path=str(self.tmp / 'no.sock'), state_dir=str(self.tmp),
                                 by_id_dir=str(self.by_id), input_dir=str(self.input),
                                 opener=self.opener_for({'event3': lambda: FakeDevice('Test receiver')}),
                                 out=out, err=err)
        self.assertEqual(code, 0, err.getvalue())
        text = out.getvalue()
        self.assertIn('Den remote', text)
        self.assertIn(rid, text)
        self.assertNotIn('70045', text)
        self.assertNotIn('70047', text)

    def test_ir_verify_reports_msc_scan_when_present(self):
        self.node('event3')
        self.link('usb-example-receiver-event-kbd', str(self.input / 'event3'))
        common = dict(socket_path=str(self.tmp / 'no.sock'), state_dir=str(self.tmp),
                      ir_config=str(self.tmp / 'ir-config.json'), by_id_dir=str(self.by_id),
                      input_dir=str(self.input), clock=FakeClock())
        both = [[FakeEvent(4, 4, 0x70045), press(115), FakeEvent(4, 4, 0x70046), press(114)]]
        out, err = io.StringIO(), io.StringIO()
        code = bose_cli.main(['ir', 'verify', BY_ID_RECEIVER], out=out, err=err,
                             opener=lambda p: FakeDevice('Test receiver', batches=[list(b) for b in both]), **common)
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn('Key codes seen: 115, 114', out.getvalue())
        self.assertIn('Scan codes seen: %d, %d' % (0x70045, 0x70046), out.getvalue())
        self.assertIn('ir learn', out.getvalue())
        scan_only = [[FakeEvent(4, 4, 0x70045), FakeEvent(4, 4, -1)]]   # no EV_KEY presses: still a success; signed values are masked
        out, err = io.StringIO(), io.StringIO()
        code = bose_cli.main(['ir', 'verify', BY_ID_RECEIVER], out=out, err=err,
                             opener=lambda p: FakeDevice('Test receiver', batches=[list(b) for b in scan_only]), **common)
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn('Scan codes seen: %d, %d' % (0x70045, 0xffffffff), out.getvalue())
        self.assertNotIn('no key presses', err.getvalue())


if __name__ == '__main__':
    unittest.main(verbosity=1)
