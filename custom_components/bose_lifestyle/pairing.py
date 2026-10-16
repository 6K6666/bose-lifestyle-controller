"""Guided pairing helpers: key generation, host-key scan and pin. Never logs; errors carry only a short code."""
import base64
import contextlib
import hashlib
import os
import re
import select
import shutil
import struct
import subprocess
import tempfile
import time
from pathlib import Path
from typing import List, Optional
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from .transport import HOST_PATTERN

KEY_NAME = 'bose_ed25519'
KNOWN_HOSTS_NAME = 'bose_known_hosts'
COMMENT = 'bose-lifestyle'
SCAN_TIMEOUT = 5
SCAN_LIMIT = 4096
ED25519_BLOB_PREFIX = struct.pack('>I', 11) + b'ssh-ed25519'

class PairingError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code

class FingerprintMismatch(PairingError):
    pass

def _public_line(key):
    line = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode('ascii').strip()
    return f'{line} {COMMENT}'

def generate_keypair(config_dir: Path) -> str:
    ssh = Path(config_dir) / '.ssh'
    ssh.mkdir(mode=0o700, exist_ok=True)
    os.chmod(ssh, 0o700)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption())
    path = ssh / KEY_NAME
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise PairingError('key_exists') from None
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(pem)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(path)   # a partial key would block pairing with key_exists forever
        raise
    return _public_line(key)

def existing_public_key(config_dir: Path) -> Optional[str]:
    try:
        data = (Path(config_dir) / '.ssh' / KEY_NAME).read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise PairingError('key_unreadable') from None
    try:
        key = serialization.load_ssh_private_key(data, password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise PairingError('key_unreadable') from None
    if not isinstance(key, Ed25519PrivateKey):
        raise PairingError('key_unreadable')
    return _public_line(key)

def environment_problems(config_dir: Path, which=shutil.which) -> List[str]:
    problems = []
    if which('ssh') is None: problems.append('ssh_missing')
    if which('ssh-keyscan') is None: problems.append('ssh_keyscan_missing')
    ssh = Path(config_dir) / '.ssh'
    try:
        with open(ssh / KEY_NAME, 'rb'): pass
    except FileNotFoundError:
        pass   # no key yet: pairing will generate one
    except OSError:   # unreadable key, or a .ssh directory this user cannot enter
        problems.append('key_unreadable')
    if not os.access(ssh if os.path.isdir(ssh) else Path(config_dir), os.W_OK | os.X_OK):
        problems.append('config_dir_unwritable')
    return problems

def _key_blob(host_key_line):
    fields = host_key_line.split()
    if len(fields) == 3: fields = fields[1:]   # "host ssh-ed25519 KEY" as ssh-keyscan prints it
    if len(fields) != 2 or fields[0] != 'ssh-ed25519': raise PairingError('no_host_key')
    try:
        blob = base64.b64decode(fields[1], validate=True)
    except ValueError:
        raise PairingError('no_host_key') from None
    if len(blob) != 51 or blob[:15] != ED25519_BLOB_PREFIX: raise PairingError('no_host_key')
    return blob

def fingerprint_of(host_key_line: str) -> str:
    digest = hashlib.sha256(_key_blob(host_key_line)).digest()
    return 'SHA256:' + base64.b64encode(digest).decode('ascii').rstrip('=')

def _run_capped(argv):
    """One process, stdout capped at SCAN_LIMIT bytes inside SCAN_TIMEOUT seconds; any failure is no_host_key."""
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        raise PairingError('no_host_key') from None
    deadline = time.monotonic() + SCAN_TIMEOUT
    output = bytearray()
    try:
        fd = process.stdout.fileno()
        while True:
            if not select.select([fd], [], [], max(deadline - time.monotonic(), 0))[0]:
                raise PairingError('no_host_key')
            chunk = os.read(fd, SCAN_LIMIT + 1)
            if not chunk: break
            output.extend(chunk)
            if len(output) > SCAN_LIMIT: raise PairingError('no_host_key')
        process.wait(timeout=max(deadline - time.monotonic(), 0.1))
        return process.returncode, bytes(output)
    except (OSError, subprocess.TimeoutExpired):
        raise PairingError('no_host_key') from None
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait()

def scan_host_key(host: str, port: int, runner=None) -> str:
    if not HOST_PATTERN.fullmatch(host): raise PairingError('invalid_host')
    if type(port) is not int or not 1 <= port <= 65535: raise PairingError('invalid_port')
    argv = ['ssh-keyscan', '-T', str(SCAN_TIMEOUT), '-t', 'ed25519', '-p', str(port), host]
    returncode, output = (_run_capped if runner is None else runner)(argv)
    if returncode != 0 or len(output) > SCAN_LIMIT: raise PairingError('no_host_key')
    try:
        lines = [line for line in output.decode('ascii').splitlines() if line.strip() and not line.startswith('#')]   # '# host:port SSH-2.0-...' banners are comments
    except UnicodeDecodeError:
        raise PairingError('no_host_key') from None
    if len(lines) != 1: raise PairingError('no_host_key')
    fields = lines[0].split()
    if len(fields) != 3 or fields[1] != 'ssh-ed25519': raise PairingError('no_host_key')
    fingerprint_of(lines[0])
    return ' '.join(fields)

def _known_host_token(token):
    match = re.fullmatch(r'\[(.+)\]:(\d{1,5})', token)   # non-default port: "[host]:port"; port 22 stays bare
    if match: return HOST_PATTERN.fullmatch(match.group(1)) is not None and 1 <= int(match.group(2)) <= 65535
    return HOST_PATTERN.fullmatch(token) is not None

def pin_host_key(host_key_line: str, known_hosts: Path, expected_fingerprint: str) -> None:
    fields = host_key_line.split()
    if len(fields) != 3 or fields[1] != 'ssh-ed25519' or not _known_host_token(fields[0]): raise PairingError('no_host_key')
    if fingerprint_of(host_key_line) != expected_fingerprint: raise FingerprintMismatch('fingerprint_mismatch')
    known_hosts = Path(known_hosts)
    token = fields[0].encode('ascii')
    new = (host_key_line + '\n').encode('ascii')
    old = known_hosts.read_bytes() if known_hosts.exists() else b''
    out, placed = [], False
    for line in old.splitlines(keepends=True):
        parts = line.split()
        if parts and parts[0] == token:   # replace the first entry for this host:port in place, drop later duplicates
            if not placed: out.append(new)
            placed = True
        else:
            out.append(line if line.endswith(b'\n') else line + b'\n')
    if not placed: out.append(new)
    fd, tmp = tempfile.mkstemp(dir=known_hosts.parent, prefix='.' + KNOWN_HOSTS_NAME + '.')   # created mode 0600
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(b''.join(out))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, known_hosts)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
