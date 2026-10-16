#!/usr/bin/env python3
"""Helpers for install-bridge.sh: the restricted authorized_keys line, device groups, platform check.

CLI:
  authorized_keys.py line --key KEY --command PATH [--from CIDR]...
  authorized_keys.py install HOME --key KEY --command PATH [--from CIDR]... [--owner USER] [--create-home]
                                                                 (prints changed|unchanged)
  authorized_keys.py group PATH
  authorized_keys.py platform [--root DIR]
  authorized_keys.py home USER
  authorized_keys.py state DIR [--owner USER]                    (prints changed|unchanged; creates DIR mode 0700 if missing)
"""
import argparse
import errno
import grp
import ipaddress
import os
import pwd
import re
import shutil
import stat
import sys
from typing import List, Optional, Sequence

KEY_RE = re.compile(r'ssh-ed25519 [A-Za-z0-9+/=]+(?: [A-Za-z0-9._@+-]+)?')
UNSAFE_PATH = re.compile(r'["\\\x00-\x1f\x7f]')


def authorized_keys_line(public_key: str, command_path: str, from_cidrs: Sequence[str] = ()) -> str:
    if not KEY_RE.fullmatch(public_key):
        raise ValueError('public key must be one ssh-ed25519 line with no extra whitespace or quotes')
    if not os.path.isabs(command_path) or UNSAFE_PATH.search(command_path):
        raise ValueError('command path must be absolute, without quotes or control characters')
    for cidr in from_cidrs:
        try:
            ipaddress.ip_network(cidr, strict=True)
        except ValueError:
            raise ValueError('bad address or CIDR for --from: ' + repr(cidr))
    options = 'restrict'
    if from_cidrs:
        options += ',from="' + ','.join(from_cidrs) + '"'
    return 'command="' + command_path + '",' + options + ' ' + public_key


def _owner_uid(owner: Optional[str]) -> Optional[int]:
    if owner is None:
        return None
    try:
        return pwd.getpwnam(owner).pw_uid
    except KeyError:
        raise ValueError('no such user for --owner: ' + owner)


def _root_parent(root: str, path: str, create: bool = True, owner_uid: Optional[int] = None):
    """Return a retained parent descriptor and basename after a no-follow walk below root."""
    root_fd = _open_root(root)
    fds = [root_fd]
    try:
        parts = [part for part in path.split('/') if part]
        if not parts or any(part in ('.', '..') for part in parts):
            raise ValueError('invalid rooted path')
        for part in parts[:-1]:
            _assert_rename_boundary(fds[-1])
            try:
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fds[-1])
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o755, dir_fd=fds[-1])
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fds[-1])
            fds.append(fd)
        _assert_rename_boundary(fds[-1], owner_uid)
        return fds, parts[-1]
    except BaseException:
        for fd in reversed(fds): os.close(fd)
        raise


def _verify_walk(root: str, path: str, fds) -> None:
    check = _open_root(root)
    try:
        if (os.fstat(check).st_dev, os.fstat(check).st_ino) != (os.fstat(fds[0]).st_dev, os.fstat(fds[0]).st_ino):
            raise ValueError('root changed during rooted write')
        parts = [part for part in path.split('/') if part][:-1]
        for i, part in enumerate(parts, 1):
            try:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=check)
            except OSError as error:
                if error.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise ValueError('parent directory changed during rooted write')
                raise
            os.close(check); check = nxt
            if (os.fstat(check).st_dev, os.fstat(check).st_ino) != (os.fstat(fds[i]).st_dev, os.fstat(fds[i]).st_ino):
                raise ValueError('parent directory changed during rooted write')
    finally:
        os.close(check)


def _root_open_dir(root: str, path: str, create: bool, create_mode: int = 0o700,
                   owner_uid: Optional[int] = None):
    """Open a rooted directory through descriptors; return all descriptors and its final fd."""
    root_fd = _open_root(root)
    fds = [root_fd]
    try:
        parts = [p for p in path.split('/') if p]
        if not parts or any(p in ('.', '..') for p in parts):
            raise ValueError('invalid rooted directory')
        created = False
        for index, part in enumerate(parts):
            _assert_rename_boundary(fds[-1])
            try:
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fds[-1])
            except FileNotFoundError:
                if not create:
                    raise ValueError('home directory is missing: ' + path + ' (create the account first, or pass --create-home)')
                final = index == len(parts) - 1
                os.mkdir(part, create_mode if final else 0o755, dir_fd=fds[-1])
                created = True
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fds[-1])
                fds.append(fd)
                if final:
                    os.fchmod(fd, create_mode)
                    if owner_uid is not None: os.fchown(fd, owner_uid, -1)
            else:
                fds.append(fd)
        return fds, created
    except OSError as error:
        for fd in reversed(fds): os.close(fd)
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError('refusing to write through a symbolic link or a non-directory: ' + path)
        raise
    except BaseException:
        for fd in reversed(fds): os.close(fd)
        raise


def _root_token(fd: int) -> str:
    st = os.fstat(fd)
    return f'{st.st_dev}:{st.st_ino}'


def _open_root(root: str) -> int:
    """Open the selected root and enforce the identity pinned by the installer shell."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    expected = os.environ.get('BOSE_INSTALL_ROOT_ID')
    if expected is not None and _root_token(fd) != expected:
        os.close(fd)
        raise ValueError('root changed during install')
    return fd


def root_identity(root: str) -> str:
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return _root_token(fd)
    finally:
        os.close(fd)


def _assert_rename_boundary(fd: int, owner_uid: Optional[int] = None) -> None:
    """Refuse parents that an untrusted group or user can use to move a child directory."""
    st = os.fstat(fd)
    allowed_owners = {0, os.geteuid()}
    if owner_uid is not None:
        allowed_owners.add(owner_uid)
    if st.st_uid not in allowed_owners or stat.S_IMODE(st.st_mode) & 0o022:
        raise ValueError('refusing a writable or untrusted rooted parent directory')


def rooted_write(root: str, path: str, data: bytes, mode: int, replace: bool = True,
                 owner_uid: Optional[int] = None) -> str:
    fds, name = _root_parent(root, path, owner_uid=owner_uid)
    parent = fds[-1]
    tmp = '.install-' + os.urandom(8).hex()
    try:
        try:
            old_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            old_fd = None
        if old_fd is not None:
            try:
                st = os.fstat(old_fd)
                if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                    raise ValueError('refusing non-regular or hard-linked rooted destination')
                if _read_all(old_fd) == data:
                    _verify_walk(root, path, fds)
                    before = os.fstat(old_fd)
                    repaired = stat.S_IMODE(before.st_mode) != mode or (owner_uid is not None and before.st_uid != owner_uid)
                    if repaired:
                        if owner_uid is not None: os.fchown(old_fd, owner_uid, -1)
                        os.fchmod(old_fd, mode)
                    _verify_walk(root, path, fds)
                    return 'changed' if repaired else 'unchanged'
            finally:
                os.close(old_fd)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data); handle.flush()
            if owner_uid is not None: os.fchown(handle.fileno(), owner_uid, -1)
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        _verify_walk(root, path, fds)
        if replace:
            os.rename(tmp, name, src_dir_fd=parent, dst_dir_fd=parent)
        else:
            os.link(tmp, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            os.unlink(tmp, dir_fd=parent)
        _verify_walk(root, path, fds)
        return 'changed'
    finally:
        try:
            try: os.unlink(tmp, dir_fd=parent)
            except FileNotFoundError: pass
        finally:
            for fd in reversed(fds): os.close(fd)


def _check_owner(st, owner_uid: Optional[int], label: str, refusal: str = 'refusing to install the key there') -> None:
    # Root (a real install), the operator running the script, and the target account may own the home and .ssh.
    allowed = {0, os.geteuid()}
    if owner_uid is not None:
        allowed.add(owner_uid)
    if st.st_uid not in allowed:
        raise ValueError(label + ' is owned by another user; ' + refusal)


def _open_dir(name: str, dir_fd: Optional[int], label: str, create: bool = False, owner_uid: Optional[int] = None):
    """Open a directory without following a symbolic link at its last component (fd-relative when dir_fd is given)."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        return os.open(name, flags, dir_fd=dir_fd)
    except FileNotFoundError:
        if not create:
            raise ValueError(label + ' is missing: ' + name + ' (create the account first, or pass --create-home)')
        os.mkdir(name, 0o700, dir_fd=dir_fd)
        fd = os.open(name, flags, dir_fd=dir_fd)
        os.fchmod(fd, 0o700)
        if owner_uid is not None:
            os.fchown(fd, owner_uid, -1)
        return fd
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError('refusing to write through a symbolic link or a non-directory: ' + label)
        raise


def ensure_state_dir(path: str, owner: Optional[str] = None, root: Optional[str] = None) -> str:
    """Create PATH with mode 0700 if missing, then remove its group and other bits. The directory is opened
    without following a link, like the key directory: a symlink is refused, and an owner other than root, the
    operator or --owner is refused before any chmod. Returns changed only when this call created it."""
    owner_uid = _owner_uid(owner)
    if root:
        rel = path[len(root):] if path.startswith(root.rstrip('/') + '/') else path
        rel = rel.lstrip('/')
        state_fds, created = _root_open_dir(root, rel, True, 0o755, owner_uid)
        fd = state_fds[-1]
        try:
            _check_owner(os.fstat(fd), owner_uid, 'state directory', 'refusing to use it')
            os.fchmod(fd, stat.S_IMODE(os.fstat(fd).st_mode) & ~0o077)
            if owner_uid is not None: os.fchown(fd, owner_uid, -1)
            _verify_walk(root, rel + '/marker', state_fds)
            return 'changed' if created else 'unchanged'
        finally:
            for state_fd in reversed(state_fds): os.close(state_fd)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    parent_fd = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        name = os.path.basename(path)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        created = False
        try:
            fd = os.open(name, flags, dir_fd=parent_fd)
        except FileNotFoundError:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            created = True
            fd = os.open(name, flags, dir_fd=parent_fd)
        except OSError as error:
            if error.errno in (errno.ELOOP, errno.ENOTDIR):
                raise ValueError('refusing to write through a symbolic link or a non-directory: state directory')
            raise
        try:
            _check_owner(os.fstat(fd), owner_uid, 'state directory', 'refusing to use it')
            os.fchmod(fd, stat.S_IMODE(os.fstat(fd).st_mode) & ~0o077)   # only removes group/other bits
            if owner_uid is not None:
                os.fchown(fd, owner_uid, -1)
        finally:
            os.close(fd)
        return 'changed' if created else 'unchanged'
    finally:
        os.close(parent_fd)


def _read_all(fd: int) -> bytes:
    chunks = []
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            return b''.join(chunks)
        chunks.append(chunk)


def create_default_config_root(root: str, path: str, owner: Optional[str] = None) -> str:
    import json
    from bose_config import default_config
    owner_uid = _owner_uid(owner)
    fds, name = _root_parent(root, path, owner_uid=owner_uid)
    try:
        try:
            existing = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fds[-1])
        except FileNotFoundError:
            existing = None
        if existing is not None:
            try:
                if not stat.S_ISREG(os.fstat(existing).st_mode):
                    raise ValueError('config.json is not a regular file')
                _verify_walk(root, path, fds)
                return 'unchanged'
            finally: os.close(existing)
    finally:
        for fd in reversed(fds): os.close(fd)
    try:
        return rooted_write(root, path, json.dumps(default_config()).encode('utf-8'), 0o600,
                            replace=False, owner_uid=owner_uid)
    except FileExistsError:
        # Another installer published first; the exclusive link keeps its contents intact.
        fds, name = _root_parent(root, path, create=False, owner_uid=owner_uid)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fds[-1])
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode): raise ValueError('config.json is not a regular file')
                _verify_walk(root, path, fds)
            finally: os.close(fd)
        finally:
            for fd in reversed(fds): os.close(fd)
        return 'unchanged'


def install_key(home: str, line: str, blob: str, owner: Optional[str] = None, create_home: bool = False,
                root: Optional[str] = None) -> str:
    """Replace any line that carries this key blob with `line` in HOME/.ssh/authorized_keys, keep other lines,
    never widen the mode. Every step is relative to a descriptor opened with O_NOFOLLOW, so a symlink planted
    in the tree (even after a check) is refused instead of followed. With root, HOME may be a logical absolute
    path below root or the corresponding physical path beginning with root; both resolve to the same destination."""
    owner_uid = _owner_uid(owner)
    root_fds = None
    if root:
        rel_home = home[len(root):] if home.startswith(root.rstrip('/') + '/') else home
        root_fds, _ = _root_open_dir(root, rel_home.lstrip('/'), create_home, 0o700, owner_uid)
        home_fd = root_fds[-1]
    else:
        home_fd = _open_dir(home, None, 'home directory', create=create_home, owner_uid=owner_uid)
    try:
        _check_owner(os.fstat(home_fd), owner_uid, 'home directory')
        if root:
            _assert_rename_boundary(home_fd, owner_uid)
        ssh_fd = _open_dir('.ssh', home_fd, '.ssh directory', create=True, owner_uid=owner_uid)
        try:
            _check_owner(os.fstat(ssh_fd), owner_uid, '.ssh directory')
            if root:
                _assert_rename_boundary(ssh_fd, owner_uid)
            os.fchmod(ssh_fd, stat.S_IMODE(os.fstat(ssh_fd).st_mode) & ~0o077)   # only removes group/other bits
            if owner_uid is not None:
                os.fchown(ssh_fd, owner_uid, -1)
            if root:
                verify_fds = root_fds + [ssh_fd]
                verify = lambda: _verify_walk(root, rel_home.lstrip('/') + '/.ssh/authorized_keys', verify_fds)
            else:
                verify = None
            return _install_in_ssh(ssh_fd, line, blob, owner_uid, verify)
        finally:
            os.close(ssh_fd)
    finally:
        if root_fds:
            for fd in reversed(root_fds): os.close(fd)
        else:
            os.close(home_fd)


def _install_in_ssh(ssh_fd: int, line: str, blob: str, owner_uid: Optional[int], verify=None) -> str:
    name = 'authorized_keys'
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=ssh_fd)
    except FileNotFoundError:
        fd = None
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise ValueError('refusing to write through a symbolic link: .ssh/authorized_keys')
        raise
    old = None
    old_mode = 0o600
    try:
        if fd is not None:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise ValueError('.ssh/authorized_keys is not a regular file')
            if st.st_nlink != 1:
                raise ValueError('.ssh/authorized_keys has more than one hard link; refusing to use it')
            _check_owner(st, owner_uid, '.ssh/authorized_keys')
            try:
                old = _read_all(fd).decode('utf-8')
            except UnicodeDecodeError:
                raise ValueError('.ssh/authorized_keys is not UTF-8 text')
            old_mode = stat.S_IMODE(st.st_mode) & 0o600
        kept = [ln for ln in (old or '').splitlines() if blob not in ln.split()]
        new = '\n'.join(kept + [line]) + '\n'
        if old is not None and new == old:
            if verify: verify()
            if owner_uid is not None:
                os.fchown(fd, owner_uid, -1)
            if verify: verify()
            return 'unchanged'
        tmp = _write_tmp(ssh_fd, new, 0o600 if old is None else old_mode, owner_uid)
        try:
            if verify: verify()
            os.rename(tmp, name, src_dir_fd=ssh_fd, dst_dir_fd=ssh_fd)
            if verify: verify()
        except BaseException:
            try: os.unlink(tmp, dir_fd=ssh_fd)
            except FileNotFoundError: pass
            raise
        return 'changed'
    finally:
        if fd is not None:
            os.close(fd)


def _write_tmp(dir_fd: int, text: str, mode: int, owner_uid: Optional[int]) -> str:
    for _ in range(100):
        name = '.authorized_keys.' + os.urandom(6).hex()
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
        except FileExistsError:
            continue
        with os.fdopen(fd, 'wb') as handle:
            handle.write(text.encode('utf-8'))
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            if owner_uid is not None:
                os.fchown(handle.fileno(), owner_uid, -1)
            os.fsync(handle.fileno())
        return name
    raise ValueError('could not create a temporary file next to authorized_keys')


def device_group(path: str) -> str:
    try:
        gid = os.stat(path).st_gid
    except OSError:
        raise ValueError('device not found: ' + path)
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        raise ValueError('cannot resolve the group that owns device: ' + path)


def user_home(name: str) -> str:
    try:
        return pwd.getpwnam(name).pw_dir
    except KeyError:
        return '/home/' + name


def check_platform(root: str, which=shutil.which, version=sys.version_info) -> List[str]:
    # With a root (staging, tests) the markers are looked up under it, so the result does not
    # depend on the machine running the check.
    problems = []
    if root:
        has_systemd = os.path.isdir(os.path.join(root, 'run/systemd/system'))
        has_udev = os.path.isdir(os.path.join(root, 'run/udev'))
    else:
        has_systemd = which('systemctl') is not None
        has_udev = which('udevadm') is not None
    if not has_systemd:
        problems.append('no_systemd')
    if not has_udev:
        problems.append('no_udev')
    if tuple(version[:2]) < (3, 9):
        problems.append('python_too_old')
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    for name in ('line', 'install'):
        p = sub.add_parser(name)
        if name == 'install':
            p.add_argument('home')
            p.add_argument('--owner', default=None)
            p.add_argument('--create-home', action='store_true')
            p.add_argument('--root', default=None)
        p.add_argument('--key', required=True)
        p.add_argument('--command', required=True)
        p.add_argument('--from', dest='froms', action='append', default=[])
    sub.add_parser('group').add_argument('path')
    sub.add_parser('home').add_argument('user')
    state = sub.add_parser('state')
    state.add_argument('path')
    state.add_argument('--owner', default=None)
    state.add_argument('--root', default=None)
    put = sub.add_parser('put')
    put.add_argument('root'); put.add_argument('path'); put.add_argument('mode', type=lambda x: int(x, 8)); put.add_argument('source')
    check = sub.add_parser('check-path'); check.add_argument('root'); check.add_argument('path')
    config = sub.add_parser('config'); config.add_argument('root'); config.add_argument('path'); config.add_argument('--owner', default=None); config.add_argument('--module-path', required=True)
    root_id = sub.add_parser('root-id'); root_id.add_argument('root')
    sub.add_parser('platform').add_argument('--root', default='')
    args = parser.parse_args(argv)
    try:
        if args.cmd == 'line':
            print(authorized_keys_line(args.key, args.command, args.froms))
        elif args.cmd == 'install':
            line = authorized_keys_line(args.key, args.command, args.froms)
            print(install_key(args.home, line, args.key.split()[1], args.owner, args.create_home, args.root))
        elif args.cmd == 'group':
            print(device_group(args.path))
        elif args.cmd == 'home':
            print(user_home(args.user))
        elif args.cmd == 'root-id':
            print(root_identity(args.root))
        elif args.cmd == 'state':
            print(ensure_state_dir(args.path, args.owner, args.root))
        elif args.cmd == 'put':
            with open(args.source, 'rb') as source:
                print(rooted_write(args.root, args.path, source.read(), args.mode))
        elif args.cmd == 'check-path':
            fd = _open_root(args.root)
            try:
                for part in [p for p in args.path.split('/') if p][:-1]:
                    try: nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    except FileNotFoundError: break
                    os.close(fd); fd = nxt
            finally: os.close(fd)
        elif args.cmd == 'config':
            sys.path.insert(0, args.module_path)
            print(create_default_config_root(args.root, args.path, args.owner))
        else:
            for code in check_platform(args.root):
                print(code)
    except (ValueError, OSError) as error:
        print(error, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
