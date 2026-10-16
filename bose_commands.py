"""LS50/M1 reported commands, RemoteCentral cnb post 23 (2020-09-30).
Room D commands are inferred and guarded until commissioned.
Other catalogued commands are live-eligible; uncaptured replies fault-latch.
BO identification is read-only.
Room volume indexes and source/mute masks below are explicitly published;
never derive one from the other. No service, memory, firmware or EEPROM commands.
"""
from typing import Dict, NamedTuple, Optional

DEFAULT_PORT = '/dev/serial/by-id/usb-PLACEHOLDER-if00-port0'
DEFAULT_BAUD = 4800
VOLUME_DOWN = b'MM 44,42,00\n'
COMMANDS = {'volume-down': VOLUME_DOWN, 'identify': b'BO\n',
            'system-power-on': b'MM 4a,41,01\n', 'system-power-off': b'MM 4a,43,01\n',
            'room-a-power-on': b'MM 4a,41,01\n', 'room-a-power-off': b'MM 4a,43,01\n'}
# (published A-C rooms; Room D index/mask are inferred and guarded)
for room, index, mask in (('a','00','01'),('b','01','02'),('c','02','04'),('d','03','08')):
    if room != 'a':
        COMMANDS[f'room-{room}-power-on'] = f'MM 4a,41,{mask}\n'.encode()
        COMMANDS[f'room-{room}-power-off'] = f'MM 4a,43,{mask}\n'.encode()
    COMMANDS[f'room-{room}-volume-up'] = f'MM 44,41,{index}\n'.encode()
    if room != 'a': COMMANDS[f'room-{room}-volume-down'] = f'MM 44,42,{index}\n'.encode()
    COMMANDS[f'room-{room}-mute'] = f'MM 43,41,{mask}\n'.encode()
    COMMANDS[f'room-{room}-unmute'] = f'MM 43,40,{mask}\n'.encode()
    for source, code in (('fm','01'),('cd','02'),('aux','03'),('video-1','04'),('video-2','05'),('tape','06'),('am','00')):
        COMMANDS[f'room-{room}-source-{source}'] = f'MM 42,40,{mask},{code}\n'.encode()
# These exact replies were captured on this M1; control and read-only results differ.
RESPONSES = {
    'system-power-off': b'\r\r\r\r>.CA4301000000\r',
    'system-power-on': b'\r\r\r\r>.CA4101000000\r',
    'room-a-power-off': b'\r\r\r\r>.CA4301000000\r',
    'room-a-power-on': b'\r\r\r\r>.CA4101000000\r',
    'room-b-power-off': b'\r\r\r\r>.CA4302000000\r',
    'room-b-power-on': b'\r\r\r\r>.CA4102000000\r',
    'room-c-power-off': b'\r\r\r\r>.CA4304000000\r',
    'room-c-power-on': b'\r\r\r\r>.CA4104000000\r',
    'room-a-source-video-1': b'\r\r\r\r>.C24001040000\r',
    'room-a-source-video-2': b'\r\r\r\r>.C24001050000\r',
    'room-a-source-aux': b'\r\r\r\r>.C24001030000\r',
    'room-b-source-aux': b'\r\r\r\r>.C24002030000\r',
    'room-c-source-aux': b'\r\r\r\r>.C24004030000\r',
    'room-b-source-video-1': b'\r\r\r\r>.C24002040000\r',
    'room-c-source-video-1': b'\r\r\r\r>.C24004040000\r',
    'room-a-source-am': b'\r\r\r\r>.C24001000000\r',
    'room-b-source-am': b'\r\r\r\r>.C24002000000\r',
    'room-c-source-am': b'\r\r\r\r>.C24004000000\r',
    'room-a-unmute': b'\r\r\r\r>.C34001000000\r',
    'room-a-mute': b'\r\r\r\r>.C34101000000\r',
    'room-b-mute': b'\r\r\r\r>.C34102000000\r',
    'room-c-mute': b'\r\r\r\r>.C34104000000\r',
    'room-b-unmute': b'\r\r\r\r>.C34002000000\r',
    'room-c-unmute': b'\r\r\r\r>.C34004000000\r',
    'room-b-volume-up': b'\r\r\r\r>.C44101000000\r',
    'room-c-volume-up': b'\r\r\r\r>.C44102000000\r',
    'room-b-volume-down': b'\r\r\r\r>.C44201000000\r',
    'room-c-volume-down': b'\r\r\r\r>.C44202000000\r',
    'volume-down': b'\r\r\r\r>.C44200000000\r',
    'room-a-volume-up': b'\r\r\r\r>.C44100000000\r',
    'identify': b'\r\rETAP Interface V1.5 [abdefrmnt] (C) 1999 Bose Corporation.\rDevice = 0001\r\r>',
}
LIVE_MESSAGES = {
    'system-power-off': 'transmitted system-power-off; P1 confirmation required',
    'system-power-on': 'transmitted system-power-on; P1 confirmation required',
    'room-a-power-off': 'transmitted room A power-off; P1 confirmation required',
    'room-a-power-on': 'transmitted room A power-on; P1 confirmation required',
    'room-a-source-video-1': 'transmitted room a source video 1; P1 confirmation required',
    'room-a-source-video-2': 'transmitted room a source video 2; P1 confirmation required',
    'room-a-source-aux': 'transmitted room a source aux; P1 confirmation required',
    'room-a-unmute': 'transmitted room a unmute; user confirmation required',
    'room-a-mute': 'transmitted room a mute; user confirmation required',
    'volume-down': 'transmitted room A volume-down; P1 confirmation required',
    'room-a-volume-up': 'transmitted room A volume-up; P1 confirmation required',
    'identify': 'identified ETAP Interface V1.5; device 0001; read-only',
}
for room in ('b','c'):
    for action in ('power-on','power-off','volume-up','volume-down','mute','unmute',
                   'source-aux','source-video-1','source-am'):
        name=f'room-{room}-{action}'
        LIVE_MESSAGES[name]=f'transmitted {name}; P1 confirmation required'
# Live only where a P1-confirmed effect and exact reply are both recorded.
# Captured replies alone never commission a control. FM/CD/Tape and B/C Video 2 were promoted 2026-10-11 (VERIFIED_EVIDENCE).
LIVE_ACTIONS = frozenset({
    'identify', 'volume-down', 'room-a-volume-up', 'room-a-mute', 'room-a-unmute',
    'room-a-source-aux', 'room-a-source-video-1', 'room-a-source-video-2', 'room-a-source-am',
    'system-power-on', 'system-power-off', 'room-a-power-on', 'room-a-power-off',
    *(f'room-{room}-{action}' for room in ('b','c') for action in (
        'power-on','power-off','volume-up','volume-down','mute','unmute',
        'source-aux','source-video-1','source-am')),
    *(f'room-{room}-source-{source}' for room in ('a','b','c') for source in ('fm','cd','tape')),
    'room-b-source-video-2', 'room-c-source-video-2',
})

# The 30 live names commissioned before the promotion mechanism. Their verification is original commissioning.
ORIGINAL_VERIFIED = frozenset({
    'room-a-mute', 'room-a-power-off', 'room-a-power-on', 'room-a-source-am', 'room-a-source-aux',
    'room-a-source-video-1', 'room-a-source-video-2', 'room-a-unmute', 'room-a-volume-up', 'room-b-mute',
    'room-b-power-off', 'room-b-power-on', 'room-b-source-am', 'room-b-source-aux', 'room-b-source-video-1',
    'room-b-unmute', 'room-b-volume-down', 'room-b-volume-up', 'room-c-mute', 'room-c-power-off',
    'room-c-power-on', 'room-c-source-am', 'room-c-source-aux', 'room-c-source-video-1', 'room-c-unmute',
    'room-c-volume-down', 'room-c-volume-up', 'system-power-off', 'system-power-on', 'volume-down',
})
# Each promotion (a name added to LIVE_ACTIONS) needs an entry: evidence file in docs/evidence/, its sha256,
# the M1 banner it was recorded against, and the date. test_bose_promotions.py enforces this.
VERIFIED_EVIDENCE: Dict[str, Dict[str, str]] = {
    'room-a-source-cd': {"evidence": 'room-a-source-cd-2026-10-11.json', "sha256": '462742dc5714abb750852fc2154a6de997ac40b05da2685d05d2348c5625f2c5', "banner": LIVE_MESSAGES["identify"], "date": "2026-10-11"},
    'room-a-source-fm': {"evidence": 'room-a-source-fm-2026-10-11.json', "sha256": 'd15e2e9c89d7475c8575df21008f5bb0642b62a0beaec64e2b07a5518177c01a', "banner": LIVE_MESSAGES["identify"], "date": "2026-10-11"},
    'room-a-source-tape': {"evidence": 'room-a-source-tape-2026-10-11.json', "sha256": '19a520a4ff3f10968687af335566aa10b265e213fece4bf0aab18a10879be122', "banner": LIVE_MESSAGES["identify"], "date": "2026-10-11"},
    'room-b-source-cd': {"evidence": 'room-b-source-cd-2026-10-11.json', "sha256": 'ce631c12d0ed4c2f392490f66636776b571521f06589eff16dd19704822351bd', "banner": LIVE_MESSAGES["identify"], "date": "2026-10-11"},
    'room-b-source-fm': {"evidence": 'room-b-source-fm-2026-10-11.json', "sha256": 'cb2ec7e7d8dbd5ac9c8eddfc9d92727b77108e6d6dd480f0aee13ff1295e9666', "banner": LIVE_MESSAGES["identify"], "date": "2026-10-11"},
    'room-b-source-tape': {"evidence": 'room-b-source-tape-2026-10-11.json', "sha256": '4fea0ef4f83dcfdc76c0ffd2eda11a86fbdb09059f8533925d9960944402c045', "banner": LIVE_MESSAGES["identify"], "date": "2026-10-11"},
    'room-b-source-video-2': {"evidence": 'room-b-source-video-2-2026-10-11.json', "sha256": '7314e26e0a484cd72602e2a7136522b3e32a8a97ae0d277e332ed55b1d690990', "banner": LIVE_MESSAGES["identify"], "date": "2026-10-11"},
    'room-c-source-cd': {"evidence": 'room-c-source-cd-2026-10-11.json', "sha256": 'a55a6ffe2ab17ef57d1d98ba1794e6ff31c36a6a35f898608770b9e720689ae2', "banner": LIVE_MESSAGES["identify"], "date": "2026-10-11"},
    'room-c-source-fm': {"evidence": 'room-c-source-fm-2026-10-11.json', "sha256": '19ffa3c8676f69fd79fb37f6dbba4d9308184b85011d4b2ae8c65fed0a59e424', "banner": LIVE_MESSAGES["identify"], "date": "2026-10-11"},
    'room-c-source-tape': {"evidence": 'room-c-source-tape-2026-10-11.json', "sha256": '9dad0cf3226599294127d337829826987f61129bf9174b66fd3894810bac730d', "banner": LIVE_MESSAGES["identify"], "date": "2026-10-11"},
    'room-c-source-video-2': {"evidence": 'room-c-source-video-2-2026-10-11.json', "sha256": '645a926ad41cdb1da17a9a16da799ac03b13e150d17a6206ddb3da2c37c606fa', "banner": LIVE_MESSAGES["identify"], "date": "2026-10-11"},
}

# Room D has no hardware today. Its commands are inferred and never in LIVE_ACTIONS; the bridge allows them only while config
# zone d and unverified.d are on, and replies are classified by the grammar below.
D_ACTIONS = frozenset(name for name in COMMANDS if name.startswith('room-d-'))
REFUSED_MESSAGE = 'refused by M1; no retry'
for _name in sorted(D_ACTIONS):
    LIVE_MESSAGES[_name] = f'transmitted {_name}; reply unverified; P1 confirmation required'
# Completes LIVE_MESSAGES for every catalogued name. Existing entries are never overwritten.
# room-a-source-am was missing: it transmitted and then fell back to a string bose_request rejects.
LIVE_MESSAGES.setdefault('room-a-source-am', 'transmitted room a source am; P1 confirmation required')
for _name in sorted(VERIFIED_EVIDENCE):
    LIVE_MESSAGES[_name] = f'transmitted {_name}; P1 confirmation required'
for _name in sorted(COMMANDS):
    LIVE_MESSAGES.setdefault(_name, f'transmitted {_name}; reply unverified; P1 confirmation required')


def expected_reply(command):
    """Control reply frame derived from the command bytes; reproduces all 30 captured A/B/C control replies."""
    fields = command.decode('ascii').strip()[3:].split(',')
    digits = ('C%X' % (int(fields[0], 16) & 0xf) + ''.join('%02X' % int(field, 16) for field in fields[1:])).ljust(12, '0')
    return b'\r\r\r\r>.' + digits.encode('ascii') + b'\r'


# Promoted commands answer with the exact derived frame, the one recorded in their evidence file.
RESPONSES.update({name: expected_reply(COMMANDS[name]) for name in VERIFIED_EVIDENCE})


def classify_reply(command, received):
    """'ok' for the exact derived frame, 'refused' when only the final status byte is F2 (the M1's error), else 'unexpected'."""
    frame = expected_reply(command)
    if received == frame: return 'ok'
    if received == frame[:-3] + b'F2\r': return 'refused'
    return 'unexpected'


# Zone comes from the wire fields: mask 01/02/04/08 (power, mute, source) or volume index 00-03.
ZONE_BY_MASK = {'01': 'a', '02': 'b', '04': 'c', '08': 'd'}
ZONE_BY_INDEX = {'00': 'a', '01': 'b', '02': 'c', '03': 'd'}
FIELD_COUNT = {'42': 4, '43': 3, '44': 3, '4a': 3}


def zone_of(command):
    """Zone for an 'MM op,field,...' command by field, or None (identify, unknown op, multi-zone mask)."""
    try:
        text = command.decode('ascii')
    except UnicodeDecodeError:
        return None
    if not text.startswith('MM ') or not text.endswith('\n'):
        return None
    fields = text[3:-1].split(',')
    if FIELD_COUNT.get(fields[0]) != len(fields):
        return None
    table = ZONE_BY_INDEX if fields[0] == '44' else ZONE_BY_MASK
    return table.get(fields[2])


class CatalogEntry(NamedTuple):
    name: str
    zone: str
    function: str
    source: Optional[str]
    verified: bool
    alias: bool


ALIASES = frozenset({'volume-down', 'system-power-on', 'system-power-off'})


def _function(name):
    if '-source-' in name:
        return 'source'
    for suffix in ('unmute', 'mute', 'power-on', 'power-off', 'volume-up', 'volume-down'):
        if name.endswith(suffix):
            return suffix
    raise ValueError(name)


CATALOG = {
    name: CatalogEntry(name, zone_of(command), _function(name),
                       name.partition('-source-')[2] or None,
                       name in LIVE_ACTIONS, name in ALIASES)
    for name, command in COMMANDS.items() if name != 'identify'
}
