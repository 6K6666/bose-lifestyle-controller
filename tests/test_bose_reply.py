"""M1 reply grammar: one derived rule reproduces every captured control reply and the F2 refusal."""
import _bootstrap
from pathlib import Path
from runpy import run_path

from bose_commands import (COMMANDS, RESPONSES, LIVE_ACTIONS, LIVE_MESSAGES, D_ACTIONS, REFUSED_MESSAGE,
                           expected_reply, classify_reply)

ROOT = Path(__file__).resolve().parents[1]

captured = [name for name in RESPONSES if name != 'identify']
assert len(captured) == 41
for name in captured:
    assert expected_reply(COMMANDS[name]) == RESPONSES[name], name

# The M1's own refusal of the invalid AM selector 07, captured 2026-10-05.
refusal = b'\r\r\r\r>.C240010700F2\r'
assert classify_reply(b'MM 42,40,01,07\n', refusal) == 'refused'

mute = COMMANDS['room-d-mute']
good = expected_reply(mute)
assert classify_reply(mute, good) == 'ok'
assert classify_reply(mute, good[:-3] + b'F2\r') == 'refused'
for bad in (b'', good[:-1], good + b'\r', expected_reply(COMMANDS['room-d-unmute']),
            expected_reply(COMMANDS['room-b-mute']), good[:-3] + b'F3\r', b'>.' + good):
    assert classify_reply(mute, bad) == 'unexpected', bad

assert len(D_ACTIONS) == 13 and all(name.startswith('room-d-') for name in D_ACTIONS)
assert D_ACTIONS == {name for name in COMMANDS if name.startswith('room-d-')}
assert D_ACTIONS.isdisjoint(LIVE_ACTIONS)
for name in D_ACTIONS:
    assert LIVE_MESSAGES[name] == f'transmitted {name}; reply unverified; P1 confirmation required'
assert REFUSED_MESSAGE == 'refused by M1; no retry'

ha = run_path(str(ROOT / 'custom_components/bose_lifestyle/commands.py'))
assert ha['COMMANDS'] == COMMANDS
assert ha['REFUSED_MESSAGE'] == REFUSED_MESSAGE
print('Reply grammar reproduces all 41 captured replies and the F2 refusal; HA command bytes match')
