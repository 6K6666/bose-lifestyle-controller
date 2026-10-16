"""IR receiver support, not tied to any receiver model.

Lists key-reporting input devices that have a stable /dev/input/by-id/ path, verifies which key codes a device
sends, writes the exact ir-config.json the bridge loads, and renders the optional udev rule from its template.
Stdlib only; evdev is imported lazily inside the default opener, so tests and CLI --help never need it.
Nothing here transmits to the Bose controller.
"""
import json
import os
import re
import select
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Callable, List, Mapping, NamedTuple, Optional, Tuple

from bose_bridge import load_ir_config
from bose_ir import KEYS

BY_ID = '/dev/input/by-id'
INPUT_DIR = '/dev/input'
EV_KEY = 1
EV_MSC = 4
MSC_SCAN = 4   # the raw scan code a remote sends before its key; learning matches these, not key codes
MAX_CODES = 32
VERIFY_SECONDS = 15.0
NAME_LIMIT = 255   # bytes: the kernel's 256-byte name buffer less the terminating NUL
TEMPLATE = Path(__file__).resolve().parent / 'deploy' / '70-bose-receiver.rules.in'
HEX4 = re.compile(r'[0-9A-Fa-f]{4}')
SERIAL = re.compile(r'[A-Za-z0-9_-]+')


class Receiver(NamedTuple):
    path: str
    name: str
    reports_keys: bool


class ReceiverGone(Exception):
    """The device vanished while listening. `codes` holds the key codes seen before it went."""

    def __init__(self, codes):
        super().__init__('receiver went away')
        self.codes = list(codes)


def _open(path):
    # Only the default opener imports evdev. Tests pass a fake opener with the same shape.
    from evdev import InputDevice
    return InputDevice(path)


def describe_receiver(path: str, opener: Optional[Callable] = None) -> Receiver:
    """Name and key capability of one device. Raises OSError when it cannot be opened."""
    device = (opener or _open)(path)
    try:
        return Receiver(path, device.name, EV_KEY in device.capabilities())
    finally:
        device.close()


def _entries(directory):
    try:
        return sorted(os.listdir(directory))
    except OSError:
        return []


def _event_target(link, input_dir):
    # A by-id entry counts only when it is a symlink to an eventN node inside the input directory.
    target = os.path.realpath(link)
    root = os.path.realpath(input_dir)
    return target if target.startswith(root + os.sep) and os.path.basename(target).startswith('event') else None


def list_receivers(by_id_dir: str = BY_ID, opener: Optional[Callable] = None,
                   input_dir: str = INPUT_DIR) -> List[Receiver]:
    found = []
    for name in _entries(by_id_dir):
        link = os.path.join(by_id_dir, name)
        if not os.path.islink(link) or _event_target(link, input_dir) is None:
            continue
        try:
            found.append(describe_receiver(link, opener))
        except OSError:
            found.append(Receiver(link, '', False))   # listed but unusable: it cannot report keys to us
    return [receiver for receiver in found if receiver.reports_keys]


def list_unsupported(input_dir: str = INPUT_DIR, by_id_dir: str = BY_ID,
                     opener: Optional[Callable] = None) -> List[Receiver]:
    """Key-reporting event devices that no by-id path points to: the bridge cannot use them."""
    covered = {os.path.realpath(os.path.join(by_id_dir, name)) for name in _entries(by_id_dir)}
    found = []
    for name in _entries(input_dir):
        path = os.path.join(input_dir, name)
        if not name.startswith('event') or os.path.realpath(path) in covered:
            continue
        with suppress(OSError):
            receiver = describe_receiver(path, opener)
            if receiver.reports_keys:
                found.append(receiver)
    return found


def verify_receiver(path: str, timeout: float = VERIFY_SECONDS, opener: Optional[Callable] = None,
                    clock: Callable[[], float] = time.monotonic, scans: Optional[List[int]] = None) -> List[int]:
    """Key codes of key presses (value 1) seen before the timeout, at most MAX_CODES. Empty on timeout.

    scans, when given, collects the MSC_SCAN values the device sends too (at most MAX_CODES).
    """
    device = (opener or _open)(path)
    codes = []
    try:
        deadline = clock() + timeout
        while len(codes) < MAX_CODES or (scans is not None and len(scans) < MAX_CODES):
            remaining = deadline - clock()
            if remaining <= 0:
                break
            ready, _, _ = select.select([device.fd], [], [], min(remaining, 0.2))
            if not ready:
                continue
            try:
                events = list(device.read())
            except BlockingIOError:
                continue
            except OSError as error:
                raise ReceiverGone(codes) from error
            for event in events:
                if event.type == EV_KEY and event.value == 1 and len(codes) < MAX_CODES:
                    codes.append(event.code)
                elif scans is not None and event.type == EV_MSC and event.code == MSC_SCAN and len(scans) < MAX_CODES:
                    scans.append(event.value & 0xffffffff)     # the bridge's own 32-bit view of the scan value
        return codes
    finally:
        device.close()


def write_ir_config(path: Path, receiver: Receiver, keys: Mapping[str, int]) -> None:
    """Write the exact bridge schema. The file is checked with the bridge's own loader before it replaces the old one."""
    path = Path(path)
    if not receiver.path.startswith(BY_ID + '/') or '..' in receiver.path.split('/'):
        raise ValueError('the receiver needs a /dev/input/by-id/ path')
    if set(keys) != set(KEYS) or any(type(keys[key]) is not int or keys[key] < 0 for key in KEYS):
        raise ValueError('volume-up, volume-down and mute need key codes')
    if not receiver.name or len(receiver.name.encode('utf-8')) > NAME_LIMIT:
        raise ValueError('the receiver name is empty or too long')
    data = {'ir_device': receiver.path, 'ir_name': receiver.name, 'ir_keys': [keys[key] for key in KEYS]}
    fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(json.dumps(data, sort_keys=True).encode('utf-8'))
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temp, 0o600)
        load_ir_config(temp)
        os.replace(temp, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(temp)
        raise
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def render_udev_rule(vendor: str, product: str, serial: Optional[str] = None) -> str:
    """The udev rule for one receiver. Only the given vendor/product (and serial) match; @GROUPS@ is left for the installer."""
    if type(vendor) is not str or not HEX4.fullmatch(vendor):
        raise ValueError('vendor must be four hex digits')
    if type(product) is not str or not HEX4.fullmatch(product):
        raise ValueError('product must be four hex digits')
    if serial is not None and (type(serial) is not str or not SERIAL.fullmatch(serial)):
        raise ValueError('serial may use letters, digits, _ and - only')
    match = '' if serial is None else ', ATTRS{serial}=="' + serial + '"'
    text = TEMPLATE.read_text(encoding='utf-8')
    return (text.replace('@VENDOR@', vendor.lower()).replace('@PRODUCT@', product.lower())
            .replace('@SERIAL_MATCH@', match))
