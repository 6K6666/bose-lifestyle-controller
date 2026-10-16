"""User config schema, strict parse, allowlist and IR target resolution.

Only user choices are data here; command bytes stay in bose_commands.py.
Does not import bose_ir (its HA copy must stay byte-identical).
"""
import copy
import errno
import json
import os
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional

from bose_commands import CATALOG

ZONES = ('a', 'b', 'c', 'd')
SOURCES = ('am', 'fm', 'cd', 'aux', 'video-1', 'video-2', 'tape')
MAX_BYTES = 4096
# Volume steps per press (volume-up and volume-down): one request writes this many ticks, each gated and reply-checked.
# The cap keeps one press from being a large, sudden volume change. The time bound is separate and enforced in bose_bridge
# (BURST_BUDGET): a press that cannot finish inside the caller's 5 s deadline stops and is reported uncertain. bose_ir.py carries the same number (a test pins both).
MAX_VOLUME_STEPS = 5


class ConfigError(ValueError):
    pass


def _reject_constant(name):
    raise ConfigError('non-finite number')


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError('duplicate key')
        result[key] = value
    return result


def _exact_object(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ConfigError('object keys')
    return value


def _bool(value):
    if type(value) is not bool:
        raise ConfigError('expected bool')
    return value


def parse_config(data: bytes) -> Dict[str, Any]:
    if type(data) is not bytes or len(data) > MAX_BYTES:
        raise ConfigError('size or type')
    if data.startswith(b'\xef\xbb\xbf'):
        raise ConfigError('BOM')
    try:
        text = data.decode('utf-8')
        obj = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)
    except ConfigError:
        raise
    except (ValueError, RecursionError):
        raise ConfigError('invalid JSON')
    # volume_steps is optional and is written only when it is not 1, so a config that never set it stays readable by
    # an older bridge (a rollback must not turn every control off).
    keys = ('schema', 'zones', 'sources', 'unverified', 'ir')
    _exact_object(obj, keys + (('volume_steps',) if type(obj) is dict and 'volume_steps' in obj else ()))
    if type(obj['schema']) is not int or obj['schema'] != 1:
        raise ConfigError('schema')
    zones = _exact_object(obj['zones'], ZONES)
    for zone in ZONES:
        _bool(_exact_object(zones[zone], ('enabled',))['enabled'])
    sources = _exact_object(obj['sources'], SOURCES)
    for source in SOURCES:
        _bool(sources[source])
    unverified = _exact_object(obj['unverified'], ZONES)
    for zone in ZONES:
        _bool(unverified[zone])
    ir_zone = _exact_object(obj['ir'], ('zone',))['zone']
    if type(ir_zone) is not str or ir_zone not in ZONES:
        raise ConfigError('ir.zone')
    if 'volume_steps' in obj and not (type(obj['volume_steps']) is int and 1 <= obj['volume_steps'] <= MAX_VOLUME_STEPS):
        raise ConfigError('volume_steps')
    return obj


def allowed_actions(config: Dict[str, Any]) -> FrozenSet[str]:
    zones, sources, unverified = config['zones'], config['sources'], config['unverified']
    return frozenset(
        name for name, entry in CATALOG.items()
        if zones[entry.zone]['enabled']
        and (entry.function != 'source' or sources[entry.source])
        and (entry.verified or unverified[entry.zone])
    )


def resolve_ir_action(key: str, zone: str, mute_state: Optional[bool]) -> str:
    if zone not in ZONES:
        raise ValueError('zone')
    if key == 'volume-up':
        return f'room-{zone}-volume-up'
    if key == 'volume-down':
        return 'volume-down' if zone == 'a' else f'room-{zone}-volume-down'
    if key == 'mute':
        return f'room-{zone}-unmute' if mute_state is True else f'room-{zone}-mute'
    raise ValueError('key')


def fast_volume_actions(zone: str) -> FrozenSet[str]:
    return frozenset({resolve_ir_action('volume-up', zone, None),
                      resolve_ir_action('volume-down', zone, None)})


# The rooms whose volume presses are paced at 0.25 s and multiplied by the volume steps. Room D has no hardware: it stays
# at one step and the one-second pacing.
VOLUME_ZONES = ('a', 'b', 'c')
VOLUME_GROUPS = tuple(fast_volume_actions(zone) for zone in VOLUME_ZONES)
VOLUME_STEP_ACTIONS = frozenset().union(*VOLUME_GROUPS)


def volume_steps(config) -> int:
    return 1 if not config else config.get('volume_steps', 1)


class CommitError(OSError):
    """kind 'not_saved': old disk and memory unchanged. kind 'uncertain': the rename may have happened."""

    def __init__(self, kind):
        super().__init__(kind)
        self.kind = kind


def _check_zone(zone):
    if type(zone) is not str or zone not in ZONES:
        raise ValueError('zone')


def _check_bool(value):
    if type(value) is not bool:
        raise ValueError('expected bool')


class ConfigStore:
    """Loads once. Setters commit to disk in order: temp write+fsync, replace, dir fsync, swap memory."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.config = None
        self._allowed = frozenset()
        self.state = 'config-invalid'
        try:
            if not stat.S_ISREG(os.lstat(self.path).st_mode):   # refuses symlinks and non-files
                return
            with open(self.path, 'rb') as handle:
                data = handle.read(MAX_BYTES + 1)
            self._install(parse_config(data))
            self.state = 'ok'
        except (OSError, ValueError):
            pass

    def _install(self, config):
        self.config = config
        self._allowed = allowed_actions(config)

    def _uncertain(self):
        self.state = 'config-uncertain'
        self.config = None
        self._allowed = frozenset()

    def allowed(self, action, dry_run=False, faulted=False) -> bool:
        if dry_run or self.state != 'ok':
            return False
        if action == 'identify':
            return not faulted
        return action in self._allowed

    def set_zone(self, zone, enabled):
        _check_zone(zone)
        _check_bool(enabled)
        self._update(lambda new: new['zones'][zone].__setitem__('enabled', enabled))

    def set_source(self, source, connected):
        if type(source) is not str or source not in SOURCES:
            raise ValueError('source')
        _check_bool(connected)
        self._update(lambda new: new['sources'].__setitem__(source, connected))

    def set_unverified(self, zone, allow):
        _check_zone(zone)
        _check_bool(allow)
        self._update(lambda new: new['unverified'].__setitem__(zone, allow))

    def set_ir_zone(self, zone):
        _check_zone(zone)
        self._update(lambda new: new['ir'].__setitem__('zone', zone))

    def set_volume_steps(self, steps):
        if type(steps) is not int or not 1 <= steps <= MAX_VOLUME_STEPS:
            raise ValueError('volume_steps')
        def mutate(new):
            if steps == 1: new.pop('volume_steps', None)   # the default is never written: the file stays old-format
            else: new['volume_steps'] = steps
        self._update(mutate)

    def _update(self, mutate):
        with self._lock:
            if self.state != 'ok':
                raise CommitError('not_saved')
            new = copy.deepcopy(self.config)
            mutate(new)
            config = parse_config(json.dumps(new).encode('utf-8'))
            self._commit(config)

    def _commit(self, config):
        data = json.dumps(config).encode('utf-8')
        directory = self.path.parent
        try:
            temp = _write_temp(directory, data)
        except OSError:
            raise CommitError('not_saved') from None
        try:
            os.replace(temp, self.path)
        except OSError:
            _unlink_quietly(temp)
            self._uncertain()
            raise CommitError('uncertain') from None
        try:
            _fsync_dir(directory)
        except OSError:
            self._uncertain()
            raise CommitError('uncertain') from None
        self._install(config)


class ConfigExists(FileExistsError):
    """create_default_config found a config (checked, or published by another writer first). Nothing was changed."""


def default_config() -> Dict[str, Any]:
    """The starting config: schema 1, every room off, no source connected, nothing unverified, IR target zone a."""
    return {
        'schema': 1,
        'zones': {zone: {'enabled': False} for zone in ZONES},
        'sources': {source: False for source in SOURCES},
        'unverified': {zone: False for zone in ZONES},
        'ir': {'zone': 'a'},
    }


LINK_UNSUPPORTED = (errno.EPERM, errno.ENOTSUP, errno.EXDEV, errno.EOPNOTSUPP)


def create_default_config(path: Path) -> None:
    """Create default_config() at path, and only if nothing is there. Same temp, fsync and directory steps as
    ConfigStore. The publish is a hard link, which never replaces an existing file. Where the filesystem has no
    hard links, an exclusive create (O_EXCL) publishes instead, which also never replaces a file."""
    path = Path(path)
    data = json.dumps(default_config()).encode('utf-8')
    parse_config(data)
    try:
        os.lstat(path)
    except FileNotFoundError:
        pass
    except OSError:
        raise CommitError('not_saved') from None
    else:
        raise ConfigExists(str(path))
    directory = path.parent
    try:
        temp = _write_temp(directory, data)
    except OSError:
        raise CommitError('not_saved') from None
    try:
        os.link(temp, path)
    except FileExistsError:
        _unlink_quietly(temp)
        raise ConfigExists(str(path)) from None
    except OSError as error:
        _unlink_quietly(temp)
        if error.errno not in LINK_UNSUPPORTED:
            raise CommitError('uncertain') from None     # not a missing-link error: the publish may have happened
        _create_exclusive(path, data)
    else:
        _unlink_quietly(temp)
    try:
        _fsync_dir(directory)
    except OSError:
        raise CommitError('uncertain') from None


def _create_exclusive(path, data: bytes) -> None:
    """Fallback publish without hard links: O_EXCL create, write and fsync. A failure removes the partial file."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ConfigExists(str(path)) from None
    except OSError:
        raise CommitError('not_saved') from None         # nothing was created
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            raise CommitError('uncertain') from None     # a partial file may remain at the path
        raise CommitError('not_saved') from None


def _write_temp(directory, data: bytes) -> str:
    """A new mode 0600 file beside the target, written and fsynced. On failure it is removed and the error propagates."""
    fd, temp = tempfile.mkstemp(dir=directory)          # mkstemp creates mode 0600
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        _unlink_quietly(temp)
        raise
    return temp


def _fsync_dir(directory) -> None:
    directory_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _unlink_quietly(path):
    try:
        os.unlink(path)
    except OSError:
        pass
