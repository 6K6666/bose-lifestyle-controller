"""Bounded JSONL event log. Logging must never affect serial control: nothing here raises."""
from contextlib import suppress
import json
import math
import os
from pathlib import Path
import time

KINDS = frozenset({'start', 'tx', 'rx', 'outcome', 'fault', 'refused', 'ir', 'serial', 'manage'})


def tail_lines(path, size=32768):
    """Last lines of a file without reading all of it (the bridge loop must not stall)."""
    try:
        with open(path, 'rb') as source:
            end = source.seek(0, os.SEEK_END)
            source.seek(max(0, end - size))
            data = source.read().decode('utf-8', 'replace')
    except OSError:
        return []
    lines = data.splitlines()
    return lines[1:] if end > size else lines


class EventLog:
    def __init__(self, path, cap=262144):
        self.path, self.cap, self.warned = (Path(path) if path else None), cap, False

    def add(self, kind, action=None, detail=''):
        if self.path is None or kind not in KINDS: return
        try:
            line = json.dumps({'t': time.time(), 'kind': kind, 'action': action if isinstance(action, str) else None,
                               'detail': str(detail)[:200]}, separators=(',', ':')) + '\n'
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try: size = self.path.stat().st_size
            except FileNotFoundError: size = 0
            if size and size + len(line) > self.cap:
                os.replace(self.path, self.path.with_name(self.path.name + '.1'))
            with open(self.path, 'a') as out: out.write(line)
        except Exception as error:
            if not self.warned:
                self.warned = True
                with suppress(Exception): print('Event log unavailable: ' + type(error).__name__, flush=True)

    def recent(self, n=50, max_bytes=8192):
        if self.path is None: return []
        events = []
        for part in (self.path.with_name(self.path.name + '.1'), self.path):
            for line in tail_lines(part):
                try: event = json.loads(line)
                except ValueError: continue
                if (isinstance(event, dict) and set(event) == {'t', 'kind', 'action', 'detail'} and event['kind'] in KINDS
                        and type(event['t']) in (int, float) and math.isfinite(event['t'])
                        and (event['action'] is None or isinstance(event['action'], str)) and isinstance(event['detail'], str)):
                    events.append(event)
        events = events[-n:]
        while events and len(json.dumps({'events': events}, separators=(',', ':'))) > max_bytes: events.pop(0)
        return events
