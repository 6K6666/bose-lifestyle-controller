#!/usr/bin/env python3
"""Fixed SSH action; minimal stdlib client, no serial imports."""
import os
import sys
import math
import socket
import time
from bose_commands import COMMANDS, LIVE_MESSAGES, REFUSED_MESSAGE
from bose_events import KINDS
from bose_ir import strict_json, validate_management, validate_status as validate_shared_status, MAX_REQUEST, MAX_REPLY
SOCKET = '/run/bose-control/command.sock'
NOT_ALLOWED = 'rejected: not-allowed'

def read_to_eof(peer, limit, timeout):
    """One total deadline and cap for a stream frame, including trailing bytes."""
    deadline = time.monotonic() + timeout
    data = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OSError('Incomplete frame; no retry')
        peer.settimeout(remaining)
        chunk = peer.recv(limit + 1 - len(data))
        if not chunk:
            return bytes(data)
        data.extend(chunk)
        if len(data) > limit:
            raise OSError('Oversized frame; no retry')

def request(path, action=b'volume-down\n'):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(5)
        client.connect(path)
        client.sendall(str(time.time()).encode() + b'\n' + action)
        client.shutdown(socket.SHUT_WR)
        try:
            reply=read_to_eof(client,MAX_REPLY if action.startswith((b'status\n',b'manage\n',b'events\n')) else 512,5).decode('ascii')
        except UnicodeError as error:
            raise OSError('Non-ASCII response; no retry') from error
        name=action.split(b'\n',1)[0].decode('ascii')
        if action==b'status\n':
            validate_status(reply)
            return reply
        if action==b'events\n':
            validate_events(reply)
            return reply
        if action==b'fault-clear\n':
            if reply!='fault cleared': raise OSError('Invalid fault-clear response; no retry')
            return reply
        if name == 'manage':
            data = strict_json(reply)
            if not isinstance(data, dict) or type(data.get('ok')) is not bool:
                raise OSError('Invalid management response; no retry')
            if set(data) != ({'ok','result'} if data['ok'] else {'ok','error'}):
                raise OSError('Invalid management response; no retry')
            return reply
        command=COMMANDS.get(name)
        dry_reply='DRY RUN; no port opened: '+command.hex(' ') if command else None
        if reply not in ('rejected', NOT_ALLOWED, 'rate-limited', 'uncertain; no retry', REFUSED_MESSAGE, LIVE_MESSAGES.get(name),
                         dry_reply):
            raise OSError('Missing or unexpected writer response; no retry')
        return reply

def validate_events(reply):
    """Bounded read-only event list; exact keys, known kinds, finite times."""
    try:
        data = strict_json(reply)
        events = data['events']
        if not isinstance(data, dict) or set(data) != {'events'} or not isinstance(events, list) or len(events) > 50: raise ValueError
        for e in events:
            if (not isinstance(e, dict) or set(e) != {'t','kind','action','detail'} or e['kind'] not in KINDS
                    or type(e['t']) not in (int, float) or not math.isfinite(e['t'])
                    or not (e['action'] is None or isinstance(e['action'], str)) or not isinstance(e['detail'], str)
                    or len(e['detail']) > 200): raise ValueError
        return events
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise OSError('Invalid events; no retry') from error


def validate_status(reply, degrade_unknown=False):
    """Bridge-side status check: the shared validator, with last actions checked against COMMANDS (identify included)."""
    try:
        return validate_shared_status(reply, degrade_unknown=degrade_unknown, action_ok=COMMANDS.__contains__)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
        raise OSError('Invalid cached status; no retry') from error


if __name__ == '__main__':
    action=os.environ.get('SSH_ORIGINAL_COMMAND')
    if action not in COMMANDS and action not in ('status','manage','events','fault-clear'):
        raise SystemExit('Rejected: only catalogued Bose actions are permitted')
    try:
        frame = (action+'\n').encode()
        if action == 'manage':
            payload = sys.stdin.buffer.read(MAX_REQUEST+1)
            if len(payload) > MAX_REQUEST: raise ValueError('Oversized request')
            validate_management(strict_json(payload.decode('utf-8')))
            frame += payload
        reply=request(SOCKET,frame)
    except (OSError, ValueError, TypeError): raise SystemExit('Bridge unavailable or invalid request; no retry')
    print(reply)
    if reply in ('rejected','uncertain; no retry'): raise SystemExit(1)   # an M1 refusal is a normal reply, exit 0
