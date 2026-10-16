"""bose-lifestyle ir learn / ir remote / ir button / ir forget: manage ops in the order HA's RemoteFlow sends them.

A stateful fake bridge answers the manage op over a temp unix socket. Nothing here opens serial, IR or a service,
and the fake fails the test if a command-name action ever arrives. Run: python3 -B test_bose_ir_learn.py
"""
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT))
from test_bose_cli_change import ScriptedBridge  # noqa: E402
import bose_cli  # noqa: E402
from bose_commands import COMMANDS  # noqa: E402
from bose_ir import KEYS  # noqa: E402

CONFIG_FLOW = ROOT / 'custom_components' / 'bose_lifestyle' / 'config_flow.py'
NAME = 'Den remote'
SCANS = {'volume-up': 0x70045, 'volume-down': 0x70046, 'mute': 0x70047}
MANAGE_OPS = {'begin', 'capture', 'session', 'choose', 'commit', 'cancel', 'update', 'delete'}


class LearnFault(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class FakeLearnBridge:
    """Same field names as bose_ir.RemoteManager.session_status. Session ids are sequential, so ops compare exactly."""

    def __init__(self, scans=SCANS, check_scans=None, mode='normal', conflict=()):
        self.scans, self.check_scans = scans, (check_scans or scans)
        self.mode, self.conflict = mode, set(conflict)   # mode 'timeout': a capture never completes
        self.session, self.profiles, self.ops, self.counter = None, {}, [], 0
        self.garble = False                               # True: the first session reply is the bridge's plain rejected

    def reply_for(self, name, body):
        if name != 'manage':
            return json.dumps({'ok': False, 'error': 'invalid_request'})
        payload = json.loads(body.decode('utf-8'))
        self.ops.append(payload)
        if self.garble and payload['op'] == 'session':
            self.garble = False
            return 'rejected'
        try:
            return json.dumps({'ok': True, 'result': self.apply(payload)}, separators=(',', ':'))
        except LearnFault as fault:
            return json.dumps({'ok': False, 'error': fault.code}, separators=(',', ':'))

    def status(self):
        s = self.session
        index = s['index']
        return {'session_id': s['session_id'], 'remote_id': s['remote_id'], 'name': s['name'], 'model': s['model'],
                'phase': s['phase'], 'candidate': s['candidate'], 'shared': False, 'verified': s['verified'],
                'key': s['keys'][index] if index < len(s['keys']) else None,
                'step': index + 1, 'steps': len(s['keys']), 'mappings': s['mappings']}

    def live(self, sid):
        if self.session is None or self.session['session_id'] != sid:
            raise LearnFault('session_expired')
        return self.session

    def capture(self, s):
        s.update(phase='waiting', candidate=None, verified=0)
        if self.mode == 'normal':
            key = s['keys'][s['index']]
            scans = self.check_scans if s['check'] else self.scans
            s.update(phase='captured', candidate=scans[key], verified=3)

    def apply(self, p):
        op = p['op']
        if op == 'update':
            profile = self.profiles[p['remote_id']]
            if 'active' in p: profile['active'] = p['active']
            if 'key' in p: profile['mappings'][p['key']]['enabled'] = p['enabled']
            return {}
        if op == 'delete':
            if self.session is not None: raise LearnFault('busy')
            del self.profiles[p['remote_id']]
            return {}
        if op == 'begin':
            if self.session is not None: raise LearnFault('busy')
            self.counter += 1
            rid = p.get('remote_id', 'd' * 32)
            if 'remote_id' in p and rid not in self.profiles: raise LearnFault('unknown_remote')
            mappings = json.loads(json.dumps(self.profiles[rid]['mappings'])) if 'remote_id' in p else {
                key: {'code': None, 'enabled': False} for key in KEYS}
            self.session = {'session_id': '%032x' % self.counter, 'remote_id': rid, 'name': p['name'],
                            'model': p['model'], 'keys': p.get('keys', list(KEYS)), 'index': 0, 'phase': 'ready',
                            'candidate': None, 'verified': 0, 'mappings': mappings, 'check': 'remote_id' in p}
            return self.status()
        s = self.live(p['session_id'])
        if op == 'capture':
            self.capture(s)
            return self.status()
        if op == 'session':
            return self.status()
        if op == 'choose':
            key = s['keys'][s['index']]
            if p['choice'] == 'retry':
                self.capture(s)
                return self.status()
            if p['choice'] == 'keep':
                if s['phase'] != 'captured': raise LearnFault('no_capture')
                if key in self.conflict: raise LearnFault('conflict')
                s['mappings'][key] = {'code': s['candidate'], 'enabled': False}
            else:
                s['mappings'][key] = {'code': None, 'enabled': False}
            s['index'] += 1
            s.update(phase='review' if s['index'] == len(s['keys']) else 'ready', candidate=None, verified=0)
            return self.status()
        if op == 'commit':
            if s['phase'] != 'review': raise LearnFault('not_ready')
            active = self.profiles.get(s['remote_id'], {}).get('active', False)
            self.profiles[s['remote_id']] = {'name': s['name'], 'model': s['model'], 'active': active,
                                             'mappings': s['mappings']}
            self.session = None
            return {'remote_id': s['remote_id']}
        if op == 'cancel':
            self.session = None
            return {}
        raise LearnFault('invalid_request')

    def sent_ops(self):
        return [p['op'] for p in self.ops]


class FakeClock:
    def __init__(self, step):
        self.now, self.step = 0.0, step

    def __call__(self):
        self.now += self.step
        return self.now


class LearnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()           # short system temp path; unix socket paths are length-limited
        self.state = os.path.join(self.tmp, 'state')
        os.mkdir(self.state)
        self.sock = os.path.join(self.tmp, 'command.sock')
        self.bridge = None
        self.prompts = []

    def tearDown(self):
        if self.bridge:
            self.bridge.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, fake):
        self.fake = fake
        self.bridge = ScriptedBridge(self.sock, fake.reply_for)

    def run_cli(self, argv, answers=(), sleep=lambda seconds: None, clock=None, socket_path=None):
        queue = list(answers)

        def ask(prompt):
            self.prompts.append(prompt)
            if not queue:
                raise AssertionError('unexpected prompt: ' + prompt)
            return queue.pop(0)

        out, err = io.StringIO(), io.StringIO()
        code = bose_cli.main(list(argv), socket_path=socket_path or self.sock, state_dir=self.state, out=out, err=err,
                             ask=ask, sleep=sleep, clock=clock or FakeClock(0.1))
        return code, out.getvalue(), err.getvalue()

    def expected_learn_ops(self, s1, s2, rid):
        learn = [{'op': 'begin', 'name': NAME, 'model': 'ir-remote'}]
        for key in KEYS:
            learn += [{'op': 'capture', 'session_id': s1}, {'op': 'session', 'session_id': s1},
                      {'op': 'choose', 'session_id': s1, 'choice': 'keep'}]
        learn.append({'op': 'commit', 'session_id': s1})
        check = [{'op': 'begin', 'name': NAME, 'model': 'ir-remote', 'remote_id': rid, 'keys': list(KEYS)}]
        for key in KEYS:
            check += [{'op': 'capture', 'session_id': s2}, {'op': 'session', 'session_id': s2},
                      {'op': 'choose', 'session_id': s2, 'choice': 'skip'}]
        check.append({'op': 'cancel', 'session_id': s2})
        return learn + check

    def test_cli_ir_learn_drives_manage_ops_in_ha_flow_order(self):
        self.start(FakeLearnBridge())
        code, out, err = self.run_cli(['ir', 'learn', '--name', NAME, '--yes'])
        self.assertEqual(code, 0, err)
        s1, s2, rid = '%032x' % 1, '%032x' % 2, 'd' * 32
        self.assertEqual(self.fake.ops, self.expected_learn_ops(s1, s2, rid))
        self.assertEqual(self.fake.sent_ops().count('commit'), 1)
        self.assertEqual(self.fake.sent_ops()[-1], 'cancel')
        self.assertIn('Ready to use', out)
        # The op names used here must be the ones HA's RemoteFlow sends (config_flow.py is the reference).
        source = CONFIG_FLOW.read_text(encoding='utf-8')
        used = set(re.findall(r"manage\('(\w+)'", source))
        self.assertTrue(set(self.fake.sent_ops()) <= used | {'session'}, (set(self.fake.sent_ops()), used))

    def test_cli_ir_learn_never_transmits(self):
        self.start(FakeLearnBridge())
        code, out, err = self.run_cli(['ir', 'learn', '--name', NAME, '--yes'])
        self.assertEqual(code, 0, err)
        frames = [name for name, _ in self.bridge.frames]
        self.assertEqual(set(frames), {'manage'})
        self.assertFalse(any(name in COMMANDS for name in frames))
        self.assertNotIn('identify', frames)
        self.assertNotIn(str(SCANS['volume-up']), out + err)     # raw scan codes stay out of plain output
        code, out, err = self.run_cli(['ir', 'learn', '--name', NAME, '--yes', '--technical'])
        self.assertIn(str(SCANS['mute']), out + err)              # and appear only under --technical

    def test_cli_ir_learn_timeout_cancels_session(self):
        self.start(FakeLearnBridge(mode='timeout'))
        code, out, err = self.run_cli(['ir', 'learn', '--name', NAME, '--yes'], clock=FakeClock(30.0))
        self.assertEqual(code, 1)
        self.assertIn('within 30 seconds', err)
        self.assertEqual(self.fake.sent_ops()[-1], 'cancel')
        self.assertNotIn('commit', self.fake.sent_ops())
        self.assertEqual(self.fake.profiles, {})

    def test_cli_ir_learn_ctrl_c_cancels_session(self):
        self.start(FakeLearnBridge(mode='timeout'))

        def interrupt(seconds):
            raise KeyboardInterrupt

        code, out, err = self.run_cli(['ir', 'learn', '--name', NAME, '--yes'], sleep=interrupt)
        self.assertEqual(code, 1)
        self.assertIn('canceled', err)
        self.assertEqual(self.fake.sent_ops()[-1], 'cancel')
        self.assertNotIn('commit', self.fake.sent_ops())

    def test_cli_ir_learn_requires_running_bridge(self):
        before = sorted(os.listdir(self.state))
        code, out, err = self.run_cli(['ir', 'learn', '--name', NAME, '--yes'], socket_path=os.path.join(self.tmp, 'none.sock'))
        self.assertEqual(code, 1)
        self.assertIn('The bridge is not running', err)
        self.assertEqual(sorted(os.listdir(self.state)), before)

    def test_cli_ir_learn_declined_at_final_prompt_cancels_without_commit(self):
        self.start(FakeLearnBridge())
        # Interactive: listen (Enter) and keep for each of the three buttons, then decline the save.
        code, out, err = self.run_cli(['ir', 'learn', '--name', NAME], answers=['', '', '', '', '', '', 'n'])
        self.assertEqual(code, 1)
        self.assertIn('Nothing was saved', out + err)
        self.assertEqual(self.fake.sent_ops()[-1], 'cancel')
        self.assertNotIn('commit', self.fake.sent_ops())

    def test_cli_ir_learn_rejects_bad_names_before_sending(self):
        self.start(FakeLearnBridge())
        for name in ('', '   ', 'x' * 41, 'bad\x01name'):
            code, out, err = self.run_cli(['ir', 'learn', '--name', name, '--yes'])
            self.assertEqual(code, 2, name)
        self.assertEqual(self.fake.ops, [])

    def test_cli_ir_learn_garbled_reply_cancels_session(self):
        fake = FakeLearnBridge()
        fake.garble = True
        self.start(fake)
        code, out, err = self.run_cli(['ir', 'learn', '--name', NAME, '--yes'])
        self.assertEqual(code, 1)
        self.assertIn('Something went wrong', err)
        self.assertEqual(fake.sent_ops()[-1], 'cancel')      # the session never stays open
        self.assertNotIn('commit', fake.sent_ops())

    def test_cli_ir_learn_conflict_asks_again_then_skip(self):
        self.start(FakeLearnBridge(conflict={'volume-up'}))
        # Interactive: volume up conflicts on keep, so it is asked again and skipped; the others are kept; save.
        code, out, err = self.run_cli(['ir', 'learn', '--name', NAME],
                                      answers=['', 'k', 's', '', '', '', '', 'y'])
        self.assertIn('This code belongs to a different button', out + err)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.fake.profiles['d' * 32]['mappings']['volume-up'], {'code': None, 'enabled': False})

    def test_cli_ir_learn_check_mismatch_stops_after_three_tries_and_keeps_remote(self):
        scans = dict(SCANS)
        self.start(FakeLearnBridge(check_scans={'volume-up': 0x1, 'volume-down': 0x2, 'mute': 0x3}))
        code, out, err = self.run_cli(['ir', 'learn', '--name', NAME, '--yes'])
        self.assertEqual(code, 1)
        self.assertIn('did not match', out + err)
        self.assertEqual(self.fake.sent_ops()[-1], 'cancel')
        self.assertEqual(self.fake.profiles['d' * 32]['mappings']['volume-up']['code'], scans['volume-up'])
        self.assertIn('is saved', out + err)

    def test_cli_ir_enable_and_disable_send_only_update_ops(self):
        fake = FakeLearnBridge()
        rid = 'e' * 32
        fake.profiles[rid] = {'name': NAME, 'model': 'ir-remote', 'active': False,
                              'mappings': {key: {'code': None, 'enabled': False} for key in KEYS}}
        self.start(fake)
        cases = [(['ir', 'remote', rid, 'on'], {'op': 'update', 'remote_id': rid, 'active': True}),
                 (['ir', 'remote', rid, 'off'], {'op': 'update', 'remote_id': rid, 'active': False}),
                 (['ir', 'button', rid, 'mute', 'on'], {'op': 'update', 'remote_id': rid, 'key': 'mute', 'enabled': True}),
                 (['ir', 'button', rid, 'mute', 'off'], {'op': 'update', 'remote_id': rid, 'key': 'mute', 'enabled': False})]
        for argv, payload in cases:
            code, out, err = self.run_cli(argv)
            self.assertEqual(code, 0, (argv, err))
        self.assertEqual(self.fake.ops, [p for _, p in cases])
        self.assertEqual(set(name for name, _ in self.bridge.frames), {'manage'})

    def test_cli_ir_forget_sends_delete_only(self):
        fake = FakeLearnBridge()
        rid = 'f' * 32
        fake.profiles[rid] = {'name': NAME, 'model': 'ir-remote', 'active': False,
                              'mappings': {key: {'code': None, 'enabled': False} for key in KEYS}}
        self.start(fake)
        code, out, err = self.run_cli(['ir', 'forget', rid])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.fake.ops, [{'op': 'delete', 'remote_id': rid}])
        self.assertEqual(fake.profiles, {})


if __name__ == '__main__':
    unittest.main(verbosity=1)
