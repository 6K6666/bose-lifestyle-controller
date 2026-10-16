"""ConfigStore: fail-closed load, serialised commit with the ordered write/replace/fsync boundaries."""
import _bootstrap
import contextlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))

from bose_commands import CATALOG  # noqa: E402
from bose_config import (ConfigExists, ConfigStore, CommitError, allowed_actions,  # noqa: E402
                         create_default_config, default_config, parse_config)
from helpers import BASELINE_CONFIG  # noqa: E402


@contextlib.contextmanager
def instrument(name, fails=lambda n: False):
    """Wrap os.<name>; call n (1-based) raises OSError when fails(n). Yields the list of calls."""
    real = getattr(os, name)
    calls = []

    def wrapper(*args, **kwargs):
        calls.append(args)
        if fails(len(calls)):
            raise OSError(5, 'injected ' + name)
        return real(*args, **kwargs)

    setattr(os, name, wrapper)
    try:
        yield calls
    finally:
        setattr(os, name, real)


def write_config(path, config=BASELINE_CONFIG):
    path.write_bytes(json.dumps(config).encode())


def names(directory):
    return sorted(p.name for p in directory.iterdir())


def test_missing_file_is_invalid_not_created():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        store = ConfigStore(path)
        assert store.state == 'config-invalid' and store.config is None
        assert store.allowed('identify') is False
        assert store.allowed('room-a-volume-up') is False
        assert not path.exists()


def test_identify_is_blocked_when_configuration_is_invalid_or_uncertain():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        store = ConfigStore(path)
        assert store.state == 'config-invalid'
        assert store.allowed('identify') is False

        write_config(path)
        store = ConfigStore(path)
        store._uncertain()
        assert store.state == 'config-uncertain'
        assert store.allowed('identify') is False


def test_invalid_file_is_invalid():
    cases = [
        b'garbage',
        b'{"schema": 1, "schema": 1}',                       # duplicate key
        b'\xef\xbb\xbf' + json.dumps(BASELINE_CONFIG).encode(),  # BOM
        b' ' * 4097,                                          # over 4096 bytes
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        for data in cases:
            path.write_bytes(data)
            store = ConfigStore(path)
            assert store.state == 'config-invalid', data[:20]
            assert store.allowed('room-a-volume-up') is False
            assert store.allowed('identify') is False
            assert path.read_bytes() == data            # invalid file is never rewritten
        real = Path(tmp) / 'real.json'
        write_config(real)
        link = Path(tmp) / 'link.json'
        link.symlink_to(real)
        assert ConfigStore(link).state == 'config-invalid'   # symlinks refused


def test_setters_refuse_when_not_ok():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        path.write_bytes(b'garbage')
        store = ConfigStore(path)
        with instrument('replace') as renames, instrument('fsync') as syncs:
            try:
                store.set_zone('a', False)
            except CommitError as error:
                assert error.kind == 'not_saved'
            else:
                raise AssertionError('setter must refuse when not ok')
        assert renames == [] and syncs == []
        assert path.read_bytes() == b'garbage' and names(Path(tmp)) == ['config.json']


def test_setter_inputs_validated_first():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        store = ConfigStore(path)                     # invalid: validation still wins over state
        for call in (lambda: store.set_zone('e', True), lambda: store.set_source('bogus', True),
                     lambda: store.set_unverified('a', 'yes'), lambda: store.set_ir_zone('A')):
            try:
                call()
            except ValueError:
                pass
            else:
                raise AssertionError('invalid input must raise ValueError')
        write_config(path)
        store = ConfigStore(path)
        for call in (lambda: store.set_zone('a', 1), lambda: store.set_zone('a', None),
                     lambda: store.set_zone('x', True)):
            try:
                call()
            except ValueError:
                pass
            else:
                raise AssertionError('non-bool or unknown zone must raise ValueError')


def test_failure_before_rename_keeps_old_state():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        write_config(path)
        before = path.read_bytes()
        store = ConfigStore(path)
        with instrument('fsync', lambda n: n == 1) as syncs, instrument('replace') as renames:
            try:
                store.set_zone('d', True)
            except CommitError as error:
                assert error.kind == 'not_saved'
            else:
                raise AssertionError('temp fsync failure must raise')
        assert len(syncs) == 1 and renames == []
        assert path.read_bytes() == before
        assert store.state == 'ok'
        assert store.allowed('room-a-volume-up') is True          # old memory still in force
        assert store.allowed('room-d-volume-up') is False
        assert names(Path(tmp)) == ['config.json']                # temp removed


def test_replace_failure_is_uncertain():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        write_config(path)
        store = ConfigStore(path)
        with instrument('replace', lambda n: True) as renames:
            try:
                store.set_zone('d', True)
            except CommitError as error:
                assert error.kind == 'uncertain'
            else:
                raise AssertionError('replace failure must raise')
        assert len(renames) == 1                                  # no retry, no second replace
        assert store.state == 'config-uncertain' and store.config is None
        assert store.allowed('room-a-volume-up') is False
        assert store.allowed('identify') is False
        assert store.allowed('identify', faulted=True) is False
        assert names(Path(tmp)) == ['config.json']
        try:
            store.set_zone('a', False)
        except CommitError as error:
            assert error.kind == 'not_saved'
        else:
            raise AssertionError('uncertain store must refuse setters')


def test_directory_fsync_failure_is_uncertain():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        write_config(path)
        store = ConfigStore(path)
        with instrument('fsync', lambda n: n == 2) as syncs, instrument('replace') as renames:
            try:
                store.set_zone('d', True)
            except CommitError as error:
                assert error.kind == 'uncertain'
            else:
                raise AssertionError('directory fsync failure must raise')
        assert len(renames) == 1 and len(syncs) == 2              # second fsync only, one rename
        assert store.state == 'config-uncertain'                  # neither claimed new nor old
        assert store.config is None
        assert store.allowed('room-a-volume-up') is False


def test_restart_after_rename_reads_new_file():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        write_config(path)
        store = ConfigStore(path)
        with instrument('fsync', lambda n: n == 2):
            try:
                store.set_zone('d', True)
            except CommitError:
                pass
        restarted = ConfigStore(path)
        assert restarted.state == 'ok'
        assert restarted.config['zones']['d']['enabled'] is True
        assert restarted.allowed('room-d-volume-up') is False     # unverified.d still off


def test_hand_edit_applies_only_to_new_store():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        write_config(path)
        store = ConfigStore(path)
        edited = json.loads(json.dumps(BASELINE_CONFIG))
        edited['zones']['a']['enabled'] = False
        write_config(path, edited)
        assert store.allowed('room-a-volume-up') is True          # running store unaffected
        assert ConfigStore(path).allowed('room-a-volume-up') is False


def test_set_ir_zone_commit_boundaries():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        write_config(path)
        store = ConfigStore(path)
        with instrument('replace') as renames:
            for bad in ('e', 'A', 1, None):
                try:
                    store.set_ir_zone(bad)
                except ValueError:
                    pass
                else:
                    raise AssertionError(bad)
        assert renames == [] and store.config['ir']['zone'] == 'a'
        with instrument('fsync', lambda n: n == 1):
            try:
                store.set_ir_zone('b')
            except CommitError as error:
                assert error.kind == 'not_saved'
        assert store.config['ir']['zone'] == 'a'
        store.set_ir_zone('b')                                     # success path
        assert store.config['ir']['zone'] == 'b' and ConfigStore(path).config['ir']['zone'] == 'b'
        with instrument('replace', lambda n: True):
            try:
                store.set_ir_zone('c')
            except CommitError as error:
                assert error.kind == 'uncertain'
        assert store.state == 'config-uncertain'


def test_file_mode_is_0600():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        write_config(path)
        ConfigStore(path).set_zone('a', False)
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_identify_exception_overrides():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        write_config(path)
        store = ConfigStore(path)
        assert store.allowed('identify') is True
        assert store.allowed('identify', dry_run=True) is False
        assert store.allowed('identify', faulted=True) is False
        assert store.allowed('room-a-volume-up', dry_run=True) is False
        assert store.allowed('no-such-action') is False


def test_temp_creation_failure_is_not_saved():
    real = tempfile.mkstemp

    def refuse(*args, **kwargs):
        raise OSError(28, 'injected mkstemp')

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        write_config(path)
        before = path.read_bytes()
        store = ConfigStore(path)
        tempfile.mkstemp = refuse
        try:
            with instrument('replace') as renames:
                try:
                    store.set_zone('d', True)
                except CommitError as error:
                    assert error.kind == 'not_saved'
                else:
                    raise AssertionError('temp creation failure must raise CommitError')
        finally:
            tempfile.mkstemp = real
        assert renames == [] and store.state == 'ok'
        assert store.allowed('room-a-volume-up') is True
        assert path.read_bytes() == before and names(Path(tmp)) == ['config.json']


def test_no_retry():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        write_config(path)
        store = ConfigStore(path)
        with instrument('fsync', lambda n: True) as syncs, instrument('replace') as renames:
            try:
                store.set_zone('d', True)
            except CommitError as error:
                assert error.kind == 'not_saved'
        assert len(syncs) == 1 and renames == []                  # one attempt, no retry loop


def test_default_config_parses_and_allows_only_power_free_set():
    config = default_config()
    assert parse_config(json.dumps(config).encode()) == config
    assert allowed_actions(config) == frozenset()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        create_default_config(path)
        store = ConfigStore(path)
        assert store.state == 'ok'
        assert store.allowed('identify') is True
        assert all(store.allowed(name) is False for name in CATALOG)


def test_create_default_never_overwrites_even_under_race():
    real_link = os.link
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'

        def racing_link(src, dst, *args, **kwargs):
            if Path(dst) == path:                                  # another writer publishes first
                path.write_bytes(b'{"theirs": true}')
            return real_link(src, dst, *args, **kwargs)

        os.link = racing_link
        try:
            try:
                create_default_config(path)
            except ConfigExists:
                pass
            else:
                raise AssertionError('a file that appears before the publish must raise ConfigExists')
        finally:
            os.link = real_link
        assert path.read_bytes() == b'{"theirs": true}'
        assert names(Path(tmp)) == ['config.json']


def test_create_default_refuses_existing_file_untouched():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        write_config(path)
        before = path.read_bytes()
        with instrument('link') as links:
            try:
                create_default_config(path)
            except ConfigExists:
                pass
            else:
                raise AssertionError('existing config must raise ConfigExists')
        assert links == [] and path.read_bytes() == before and names(Path(tmp)) == ['config.json']


def test_create_default_mode_0600_and_fsyncs():
    real_fsync = os.fsync
    synced_dirs = []

    def recording_fsync(fd):
        synced_dirs.append(stat.S_ISDIR(os.fstat(fd).st_mode))   # False for the temp file, True for the directory
        return real_fsync(fd)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        os.fsync = recording_fsync
        try:
            create_default_config(path)
        finally:
            os.fsync = real_fsync
        assert synced_dirs == [False, True]
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert json.loads(path.read_bytes()) == default_config()
        assert names(Path(tmp)) == ['config.json']


def test_create_failure_before_publish_is_not_saved_and_leaves_no_temp():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        with instrument('fsync', lambda n: n == 1) as syncs, instrument('link') as links:
            try:
                create_default_config(path)
            except CommitError as error:
                assert error.kind == 'not_saved'
            else:
                raise AssertionError('a failed temp write must raise CommitError not_saved')
        assert links == [] and len(syncs) == 1
        assert not path.exists() and names(Path(tmp)) == []


def test_publish_or_dir_fsync_failure_is_uncertain_no_retry():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        with instrument('link', lambda n: True) as links:
            try:
                create_default_config(path)
            except CommitError as error:
                assert error.kind == 'uncertain'
            else:
                raise AssertionError('a failed publish must raise CommitError uncertain')
        assert len(links) == 1 and names(Path(tmp)) == []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        with instrument('fsync', lambda n: n == 2) as syncs:
            try:
                create_default_config(path)
            except CommitError as error:
                assert error.kind == 'uncertain'
            else:
                raise AssertionError('a failed directory fsync must raise CommitError uncertain')
        assert len(syncs) == 2                                    # no retry after the uncertain commit
        assert json.loads(path.read_bytes()) == default_config()


def test_setters_still_refuse_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'config.json'
        store = ConfigStore(path)
        changes = [lambda: store.set_zone('a', True), lambda: store.set_source('aux', True),
                   lambda: store.set_unverified('a', True), lambda: store.set_ir_zone('b')]
        with instrument('replace') as renames:
            for change in changes:
                try:
                    change()
                except CommitError as error:
                    assert error.kind == 'not_saved'
                else:
                    raise AssertionError('a missing config must refuse every setter')
        assert renames == [] and not path.exists() and names(Path(tmp)) == []


def _bridge_startup(dry_run):
    """Start serve() in a child process with no config.json, let it bind, stop it; return whether config.json appeared."""
    with tempfile.TemporaryDirectory() as tmp:
        sock = os.path.join(tmp, 'command.sock')
        state = os.path.join(tmp, 'ir-state.json')
        code = ("import sys;sys.modules['serial']=None;sys.modules['evdev']=None;"
                "from bose_bridge import serve;serve(sys.argv[1],sys.argv[2]=='dry',ir_state=sys.argv[3])")
        proc = subprocess.Popen([sys.executable, '-u', '-c', code, sock, 'dry' if dry_run else 'live', state],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(ROOT))
        try:
            for _ in range(300):
                if proc.poll() is not None:
                    raise AssertionError(proc.stderr.read())
                if os.path.exists(sock):
                    break
                time.sleep(.02)
            else:
                raise AssertionError('bridge did not bind its socket')
            time.sleep(.2)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
        return os.path.lexists(os.path.join(tmp, 'config.json'))


def test_bridge_startup_never_creates_config():
    assert _bridge_startup(dry_run=True) is False
    assert _bridge_startup(dry_run=False) is False


def test_create_default_falls_back_without_hardlinks_and_never_overwrites():
    import errno
    real_link = os.link
    for code in (errno.EPERM, errno.ENOTSUP, errno.EXDEV, errno.EOPNOTSUPP):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'

            def no_links(src, dst, *args, **kwargs):
                raise OSError(code, 'no hard links here')

            os.link = no_links
            try:
                create_default_config(path)
            finally:
                os.link = real_link
            assert json.loads(path.read_bytes()) == default_config()
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
            assert names(Path(tmp)) == ['config.json']

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'

            def racing_no_links(src, dst, *args, **kwargs):
                if Path(dst) == path:                              # another writer publishes first, then links fail
                    path.write_bytes(b'{"theirs": true}')
                raise OSError(code, 'no hard links here')

            os.link = racing_no_links
            try:
                try:
                    create_default_config(path)
                except ConfigExists:
                    pass
                else:
                    raise AssertionError('the fallback must never overwrite a file that appeared first')
            finally:
                os.link = real_link
            assert path.read_bytes() == b'{"theirs": true}'
            assert names(Path(tmp)) == ['config.json']


def test_create_default_link_unsupported_failure_is_not_saved():
    import errno
    real_link_for_tests = os.link
    real_open = os.open
    for code in (errno.EPERM, errno.EXDEV, errno.ENOTSUP, errno.EOPNOTSUPP):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'

            def no_links(src, dst, *args, **kwargs):
                raise OSError(code, 'no hard links here')

            def failing_exclusive_open(target, flags, *args, **kwargs):
                if Path(target) == path and flags & os.O_EXCL:
                    raise OSError(errno.EIO, 'injected fallback open')
                return real_open(target, flags, *args, **kwargs)

            os.link = no_links
            os.open = failing_exclusive_open
            try:
                create_default_config(path)
            except CommitError as error:
                assert error.kind == 'not_saved', error.kind
            else:
                raise AssertionError('a failure before anything was published must be not_saved')
            finally:
                os.link = real_link_for_tests
                os.open = real_open
            assert not path.exists() and names(Path(tmp)) == []


if __name__ == '__main__':
    tests = [value for name, value in sorted(globals().items()) if name.startswith('test_')]
    for test in tests:
        test()
    print(f'{len(tests)} config commit tests passed')
