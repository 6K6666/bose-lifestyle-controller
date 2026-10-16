"""Explicit recovery for a damaged ir-state.json. The file holds the bridge's pairing identity and the learned
remotes, so nothing here ever deletes or silently replaces it: every change first preserves the damaged bytes in a
sibling file, and replacing it takes an explicit operator command. Offline only (the CLI refuses while a bridge runs)."""
import os
import json
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Dict, Optional

from bose_ir import IRStateError, RemoteManager

IDENTITY = re.compile(rb'"([0-9a-f]{32})"')   # matched on the raw bytes: a damaged byte inside the id must not be skipped
TRUNCATED = object()
INCOMPLETE_LITERALS = (b'n', b'nu', b'nul', b't', b'tr', b'tru', b'f', b'fa', b'fal', b'fals')
_MAX_JSON_NESTING = 256  # shortcut: state JSON is shallow; raise only if a supported format needs more.


def _string_end(data: bytes, start: int):
    """Return the byte after a JSON string, TRUNCATED at EOF, or None when malformed."""
    if start >= len(data) or data[start] != 34:
        return None
    i = start + 1
    while i < len(data):
        byte = data[i]
        if byte == 34:
            return i + 1
        if byte < 32:
            return None
        if byte == 92:
            i += 1
            if i >= len(data):
                return TRUNCATED
        i += 1
    return TRUNCATED


def _value_end(data: bytes, start: int):
    """Return the byte after one JSON value, TRUNCATED at EOF, or None when malformed."""
    if start >= len(data):
        return TRUNCATED
    if data[start] == 34:
        end = _string_end(data, start)
        if end is None or end is TRUNCATED:
            return end
        try: json.loads(data[start:end])
        except (UnicodeDecodeError, ValueError, RecursionError): return None
        return end
    if data[start] not in (123, 91):
        end = start
        while end < len(data) and data[end] not in b',]} \t\r\n':
            end += 1
        if end == start: return None
        if end == len(data) and data[start:end] in INCOMPLETE_LITERALS:
            return TRUNCATED
        try: json.loads(data[start:end])
        except (UnicodeDecodeError, ValueError, RecursionError): return None
        return end if end < len(data) else TRUNCATED
    stack = [125 if data[start] == 123 else 93]
    i = start + 1
    while i < len(data) and stack:
        byte = data[i]
        if byte == 34:
            end = _string_end(data, i)
            if end is None or end is TRUNCATED:
                return end
            i = end
            continue
        if byte in (123, 91):
            if len(stack) >= _MAX_JSON_NESTING: return None
            stack.append(125 if byte == 123 else 93)
        elif byte in (125, 93):
            if byte != stack[-1]:
                return None
            stack.pop()
        i += 1
    if stack: return TRUNCATED
    try: json.loads(data[start:i])
    except (UnicodeDecodeError, ValueError, RecursionError): return None
    return i


class StateRecoveryError(Exception):
    """Carries a short code only (never file content)."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _raw_identity(path: Path) -> Optional[str]:
    try: data = path.read_bytes()
    except OSError: return None
    i = 0
    while i < len(data) and data[i] in b' \t\r\n': i += 1
    if i >= len(data) or data[i] != 123: return None
    found = []
    i += 1
    after_comma = False
    while i < len(data):
        while i < len(data) and data[i] in b' \t\r\n': i += 1
        if i >= len(data): break
        if data[i] == 125: return None if after_comma else (found[0].decode('ascii') if len(found) == 1 and found[0] is not None else None)
        key_end = _string_end(data, i)
        if key_end is None: return None
        if key_end is TRUNCATED: break
        try: key = json.loads(data[i:key_end].decode('utf-8'))
        except (UnicodeDecodeError, ValueError, RecursionError): return None
        after_comma = False
        i = key_end
        while i < len(data) and data[i] in b' \t\r\n': i += 1
        if i >= len(data): break
        if data[i] != 58: return None
        i += 1
        while i < len(data) and data[i] in b' \t\r\n': i += 1
        value_start = i
        value_end = _value_end(data, value_start)
        if value_end is None: return None
        if value_end is TRUNCATED:
            if key == 'bridge_id': found.append(None)
            break
        if key == 'bridge_id':
            identity = IDENTITY.fullmatch(data[value_start:value_end])
            found.append(identity.group(1) if identity else None)
        i = value_end
        while i < len(data) and data[i] in b' \t\r\n': i += 1
        if i >= len(data): break
        if data[i] == 125: break
        if data[i] != 44: return None
        i += 1
        after_comma = True
        continue
    return found[0].decode('ascii') if len(found) == 1 and found[0] is not None else None


def inspect(path) -> Dict[str, object]:
    """Read-only. {'exists', 'ok', 'reason', 'identity_recoverable', 'profiles'}; never the identity itself."""
    path = Path(path)
    if not os.path.lexists(path):
        return {'exists': False, 'ok': True, 'reason': None, 'identity_recoverable': False, 'profiles': 0}
    try:
        manager = RemoteManager(path)
    except IRStateError as error:
        return {'exists': True, 'ok': False, 'reason': error.reason,
                'identity_recoverable': _raw_identity(path) is not None, 'profiles': 0}
    return {'exists': True, 'ok': True, 'reason': None, 'identity_recoverable': True, 'profiles': len(manager.profiles)}


def _preserve(path: Path) -> Path:
    """Copy the damaged bytes beside the file, never overwriting an earlier copy."""
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    for attempt in range(100):
        copy = path.with_name(f'{path.name}.damaged-{stamp}' + (f'-{attempt}' if attempt else ''))
        if path.is_symlink():
            # A link (even a dangling one) is copied as a link, exclusively; the original entry stays in place until the
            # new file atomically replaces it, so a failed install never leaves the path absent.
            try: os.symlink(os.readlink(path), copy)
            except FileExistsError: continue
            return copy
        try: fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError: continue
        try:
            try: damaged = path.read_bytes()
            except OSError as error: raise StateRecoveryError('cannot-preserve') from error
            with os.fdopen(fd, 'wb') as out:
                out.write(damaged)
                out.flush(); os.fsync(out.fileno())
        except BaseException:
            try: os.unlink(copy)
            except OSError: pass
            raise
        return copy
    raise StateRecoveryError('cannot-preserve')


def _install(path: Path, data: bytes) -> None:
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.ir-state.')
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def _require_damaged(path: Path) -> None:
    state = inspect(path)
    if not state['exists']: raise StateRecoveryError('no-state-file')
    if state['ok']: raise StateRecoveryError('state-is-fine')


def restore(path, backup) -> Path:
    """Replace the damaged file with a backup that loads cleanly. Returns the preserved copy of the damaged file."""
    path, backup = Path(path), Path(backup)
    _require_damaged(path)
    try: RemoteManager(backup)                              # the backup must pass the same strict load
    except IRStateError as error: raise StateRecoveryError('backup-' + error.reason) from error
    except OSError as error: raise StateRecoveryError('backup-unreadable') from error
    copy = _preserve(path)
    _install(path, backup.read_bytes())
    return copy


def reset(path, keep_identity: bool) -> Path:
    """Start with no learned remotes. keep_identity keeps the pairing identity found in the damaged file (refused
    when it cannot be read with certainty); otherwise a NEW identity is created and Home Assistant must be paired again."""
    path = Path(path)
    _require_damaged(path)
    if keep_identity:
        bridge_id = _raw_identity(path)
        if bridge_id is None: raise StateRecoveryError('identity-not-recoverable')
    else:
        bridge_id = uuid.uuid4().hex
    copy = _preserve(path)
    _install(path, json.dumps({'schema': 2, 'bridge_id': bridge_id, 'profiles': {}, 'mute_state': None}).encode('utf-8'))
    return copy
