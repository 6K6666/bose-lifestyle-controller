"""Pairing helpers: key generation, public key, environment checks, host-key scan and pin. Needs the HA test venv."""
import base64
import hashlib
import logging
import os
import stat
import struct
import tempfile
from pathlib import Path
from custom_components.bose_lifestyle import pairing
from custom_components.bose_lifestyle.pairing import PairingError, FingerprintMismatch

HOST = 'bridge.example.invalid'
PUB = bytes(range(32))
BLOB = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + PUB
LINE = 'ssh-ed25519 ' + base64.b64encode(BLOB).decode()


def fingerprint(blob):
    return 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')


def expect_code(code, call):
    try:
        call()
    except PairingError as error:
        assert str(error) == code, (str(error), code)
        assert error.__cause__ is None   # no chained OS or base64 error is attached as a cause
        return error
    raise AssertionError(('accepted', code))


def test_fingerprint_of_known_vector():
    expected = fingerprint(BLOB)
    assert pairing.fingerprint_of(LINE) == expected
    assert pairing.fingerprint_of(HOST + ' ' + LINE) == expected
    expect_code('no_host_key', lambda: pairing.fingerprint_of('ssh-rsa ' + LINE.split()[1]))
    expect_code('no_host_key', lambda: pairing.fingerprint_of('ssh-ed25519 !!notbase64'))


def test_generate_keypair_mode_0600_and_refuses_overwrite():
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp)
        public = pairing.generate_keypair(config)
        ssh, key = config / '.ssh', config / '.ssh' / 'bose_ed25519'
        assert stat.S_IMODE(os.stat(ssh).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(key).st_mode) == 0o600
        fields = public.split()
        assert len(fields) == 3 and fields[0] == 'ssh-ed25519' and fields[2] == 'bose-lifestyle' and '\n' not in public
        before = key.read_bytes()
        expect_code('key_exists', lambda: pairing.generate_keypair(config))
        assert key.read_bytes() == before
        assert sorted(os.listdir(ssh)) == ['bose_ed25519']


def test_existing_public_key_matches_generated():
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp)
        assert pairing.existing_public_key(config) is None
        public = pairing.generate_keypair(config)
        assert pairing.existing_public_key(config) == public


def test_pairing_flow_generates_key_once_and_never_logs_private_key():
    records = []
    class Capture(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))
    handler = Capture(logging.DEBUG)
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)
            public = pairing.generate_keypair(config)
            text = (config / '.ssh' / 'bose_ed25519').read_text()
            secret = [line for line in text.splitlines() if line and not line.startswith('-----')]
            assert len(secret) > 3
            error = expect_code('key_exists', lambda: pairing.generate_keypair(config))
            seen = '\n'.join(records) + repr(error) + str(error) + public + repr(pairing.existing_public_key(config))
            for line in secret:
                assert line not in seen
            assert 'PRIVATE KEY' not in seen
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


def test_environment_problems_reports_plain_codes():
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp)
        pairing.generate_keypair(config)
        present = lambda name: '/usr/bin/' + name
        assert pairing.environment_problems(config, which=present) == []
        assert pairing.environment_problems(config, which=lambda name: None) == ['ssh_missing', 'ssh_keyscan_missing']


def test_missing_ssh_binary_reports_plain_error():
    with tempfile.TemporaryDirectory() as tmp:
        assert 'ssh_missing' in pairing.environment_problems(Path(tmp), which=lambda name: None)


def test_unreadable_key_reports_plain_error():
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp)
        pairing.generate_keypair(config)
        key = config / '.ssh' / 'bose_ed25519'
        key.unlink()
        key.mkdir()   # a directory where the key file should be: open() fails for any user, including root
        assert pairing.environment_problems(config, which=lambda name: '/x') == ['key_unreadable']


def test_host_key_pin_mismatch_refused():
    with tempfile.TemporaryDirectory() as tmp:
        ssh = Path(tmp) / '.ssh'
        ssh.mkdir(mode=0o700)
        known = ssh / 'bose_known_hosts'
        known.write_text('other.example.invalid ssh-ed25519 AAAA\n')
        before = sorted(os.listdir(ssh))
        try:
            pairing.pin_host_key(HOST + ' ' + LINE, known, 'SHA256:WRONG')
        except FingerprintMismatch as error:
            assert str(error) == 'fingerprint_mismatch'
        else:
            raise AssertionError('mismatched fingerprint pinned')
        assert known.read_text() == 'other.example.invalid ssh-ed25519 AAAA\n'
        assert sorted(os.listdir(ssh)) == before
        assert issubclass(FingerprintMismatch, PairingError)


def test_pin_writes_exact_line_mode_0600_replaces_previous():
    with tempfile.TemporaryDirectory() as tmp:
        ssh = Path(tmp) / '.ssh'
        ssh.mkdir(mode=0o700)
        known = ssh / 'bose_known_hosts'
        known.write_text('other.example.invalid ssh-ed25519 X\n'
                         + HOST + ' ssh-ed25519 OLD\n'
                         + '[' + HOST + ']:2222 ssh-ed25519 OLDPORT\n')
        new = HOST + ' ' + LINE
        pairing.pin_host_key(new, known, pairing.fingerprint_of(new))
        assert known.read_text() == 'other.example.invalid ssh-ed25519 X\n' + HOST + ' ' + LINE + '\n' + '[' + HOST + ']:2222 ssh-ed25519 OLDPORT\n'
        assert stat.S_IMODE(os.stat(known).st_mode) == 0o600
        # A non-default port is its own entry: the bracketed line replaces only itself.
        port_line = '[' + HOST + ']:2222 ' + LINE
        pairing.pin_host_key(port_line, known, pairing.fingerprint_of(port_line))
        assert known.read_text() == 'other.example.invalid ssh-ed25519 X\n' + HOST + ' ' + LINE + '\n' + '[' + HOST + ']:2222 ' + LINE + '\n'
        assert sorted(os.listdir(ssh)) == ['bose_known_hosts']
        # Lines that are not a single ed25519 host entry are refused before the file is touched.
        before = known.read_bytes()
        for bad in (HOST + ' ' + LINE + '\nx', '|1|abc= ' + LINE, '@cert-authority ' + HOST + ' ' + LINE, HOST + ' ssh-rsa ' + LINE.split()[1]):
            expect_code('no_host_key', lambda: pairing.pin_host_key(bad, known, pairing.fingerprint_of(LINE)))
        assert known.read_bytes() == before


class Runner:
    def __init__(self, returncode=0, stdout=b''):
        self.returncode, self.stdout, self.calls = returncode, stdout, []
    def __call__(self, argv):
        self.calls.append(list(argv))
        return self.returncode, self.stdout


def test_scan_host_key_is_argv_vector_single_attempt():
    runner = Runner(0, (HOST + ' ' + LINE + '\n').encode())
    assert pairing.scan_host_key(HOST, 2222, runner=runner) == HOST + ' ' + LINE
    assert runner.calls == [['ssh-keyscan', '-T', '5', '-t', 'ed25519', '-p', '2222', HOST]]
    assert pairing.scan_host_key(HOST, 22, runner=runner) == HOST + ' ' + LINE
    assert runner.calls[-1] == ['ssh-keyscan', '-T', '5', '-t', 'ed25519', '-p', '22', HOST]
    assert len(runner.calls) == 2


def test_scan_host_key_refuses_option_like_host_before_running():
    runner = Runner(0, (HOST + ' ' + LINE + '\n').encode())
    for host in ('-oProxyCommand=id', 'bad host', 'a;b', ''):
        expect_code('invalid_host', lambda: pairing.scan_host_key(host, 22, runner=runner))
    expect_code('invalid_port', lambda: pairing.scan_host_key(HOST, 0, runner=runner))
    assert runner.calls == []


def test_scan_host_key_skips_banner_comment():
    # ssh-keyscan may print a '# host:port SSH-2.0-...' banner next to the key line; it is not a key.
    runner = Runner(0, ('# ' + HOST + ':22 SSH-2.0-OpenSSH_9.6\n' + HOST + ' ' + LINE + '\n').encode())
    assert pairing.scan_host_key(HOST, 22, runner=runner) == HOST + ' ' + LINE
    two = Runner(0, ('# banner\n' + HOST + ' ' + LINE + '\n' + HOST + ' ' + LINE + '\n').encode())
    expect_code('no_host_key', lambda: pairing.scan_host_key(HOST, 22, runner=two))


def test_scan_host_key_rejects_bad_output():
    ed = (HOST + ' ' + LINE + '\n').encode()
    rsa = (HOST + ' ssh-rsa ' + LINE.split()[1] + '\n').encode()
    for runner in (Runner(0, ed + ed), Runner(0, rsa), Runner(1, ed), Runner(0, b''), Runner(0, b'x' * 5000)):
        expect_code('no_host_key', lambda: pairing.scan_host_key(HOST, 22, runner=runner))


def test_default_scan_runner_caps_output_with_fake_binary():
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / 'ssh-keyscan'
        script.write_text('#!/bin/sh\nhead -c 6000 /dev/zero\n')
        script.chmod(0o755)
        old = os.environ['PATH']
        os.environ['PATH'] = tmp + os.pathsep + old
        try:
            expect_code('no_host_key', lambda: pairing.scan_host_key(HOST, 22))
            script.write_text('#!/bin/sh\necho "' + HOST + ' ' + LINE + '"\n')
            assert pairing.scan_host_key(HOST, 22) == HOST + ' ' + LINE
        finally:
            os.environ['PATH'] = old


if __name__ == '__main__':
    for name, test in sorted(globals().items()):
        if name.startswith('test_') and callable(test):
            test()
    print('Pairing key generation, environment codes, host-key scan and pin checks passed')
