"""Bridge gate: one allowed() callable decides submit, the write-time recheck and the wire budget."""
import _bootstrap
import math
import sys
from unittest.mock import patch
with patch.dict(sys.modules, {'serial': None, 'evdev': None}):
    from bose_bridge import Bridge
from bose_commands import COMMANDS, LIVE_ACTIONS

FAST = frozenset({'volume-down', 'room-a-volume-up'})


def make(allowed, writes, now, fast=lambda: FAST):
    def writer(action, data):
        writes.append((action, data))
        return 'ok'
    return Bridge(writer, clock=lambda: now[0], allowed=allowed, fast_actions=fast)


def test_denied_action_spends_no_wire_budget():
    now, writes, allow = [0.0], [], set()
    b = make(lambda a: a in allow, writes, now)
    assert not b.submit('volume-down')                      # only the gate can refuse here: no prior transmit, clock at 0
    assert b.pending is None
    assert b.handle_one() is None
    assert writes == [] and b.last == -math.inf and b.last_action is None
    allow.add('volume-down')
    assert b.submit('volume-down')                          # the denied attempt took no slot and no budget


def test_rejected_not_allowed_restores_last():
    now, seen = [0.0], []
    replies = iter(['ok', 'rejected: not-allowed'])
    def writer(action, data):
        seen.append(action)
        return next(replies)
    b = Bridge(writer, clock=lambda: now[0], allowed=LIVE_ACTIONS.__contains__, fast_actions=lambda: FAST)
    assert b.submit('volume-down'); b.handle_one()
    assert b.last == 0.0 and b.last_action == 'volume-down'
    now[0] = .3
    assert b.submit('room-a-volume-up'); b.handle_one()     # writer answers the new token
    assert seen == ['volume-down', 'room-a-volume-up']
    assert b.last == 0.0 and b.last_action == 'volume-down', 'rejected token must restore the budget'


def test_fast_interval_uses_callable():
    now, writes = [0.0], []
    current = [frozenset()]
    b = make(LIVE_ACTIONS.__contains__, writes, now, fast=lambda: current[0])
    assert b.submit('volume-down'); b.handle_one()
    now[0] = .3
    assert not b.submit('room-a-volume-up')                 # empty fast set: one-second interval
    current[0] = frozenset({'volume-down', 'room-a-volume-up'})
    assert b.submit('room-a-volume-up'); b.handle_one()     # callable now says fast: 250 ms is enough
    assert [a for a, _ in writes] == ['volume-down', 'room-a-volume-up']


def test_shared_allowed_callable():
    now, writes, calls = [0.0], [], []
    state = {'allow': True}

    def shared(action):
        calls.append(action)
        return state['allow'] and action in LIVE_ACTIONS

    b = make(shared, writes, now)
    assert b.allowed is shared
    assert b.submit('volume-down')
    assert calls == ['volume-down'], 'submit must consult the callable'
    b.handle_one()
    assert calls == ['volume-down', 'volume-down'], 'write-time recheck must consult the same callable'
    assert writes == [('volume-down', COMMANDS['volume-down'])]
    # Disabled after submit: the write-time recheck discards the pending action with zero bytes.
    now[0] = 2.0
    assert b.submit('room-a-mute')
    state['allow'] = False
    assert b.handle_one() == 'rejected: not-allowed'
    assert writes == [('volume-down', COMMANDS['volume-down'])], 'no bytes may be written for a disallowed action'
    assert b.pending is None and b.last_action == 'volume-down'


if __name__ == '__main__':
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            fn()
    print('Bridge gate: denied actions spend no budget; one allowed() callable; write-time recheck passed')
