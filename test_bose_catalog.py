"""Catalog zones come from wire bytes; verified flags are pinned."""
import json
from pathlib import Path

import sys

from bose_commands import COMMANDS, LIVE_ACTIONS, zone_of, CATALOG, ALIASES
from bose_config import allowed_actions

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tests"))
from helpers import BASELINE_CONFIG  # noqa: E402

assert zone_of(b'MM 44,41,00\n') == 'a'
assert zone_of(b'MM 44,42,03\n') == 'd'
assert zone_of(b'MM 4a,41,04\n') == 'c'
assert zone_of(b'MM 43,40,02\n') == 'b'
assert zone_of(b'MM 42,40,08,03\n') == 'd'
assert zone_of(b'BO\n') is None
assert zone_of(b'MM 4a,41,03\n') is None
assert zone_of(b'MM 45,41,01\n') is None
print('zone_of parses fields for every case and rejects identify, multi-zone and unknown ops')

assert set(CATALOG) == set(COMMANDS) - {'identify'}
for name, entry in CATALOG.items():
    assert entry.zone == zone_of(COMMANDS[name]), name
assert {n for n, e in CATALOG.items() if e.alias} == set(ALIASES) == {'volume-down', 'system-power-on', 'system-power-off'}
assert all(CATALOG[n].zone == 'a' for n in ALIASES)
print('catalog covers every command except identify; zones match bytes; aliases are exact')

verified = {n for n, e in CATALOG.items() if e.verified}
assert len(verified) == 41 and verified == LIVE_ACTIONS - {'identify'}
unverified_abc = {n for n, e in CATALOG.items() if not e.verified and e.zone != 'd'}
assert unverified_abc == set()
assert all(not e.verified for n, e in CATALOG.items() if n.startswith('room-d-'))
assert allowed_actions(BASELINE_CONFIG) == {n for n in verified if CATALOG[n].function != 'source' or BASELINE_CONFIG['sources'][CATALOG[n].source]}
print('baseline config allows exactly the verified live commands whose source switch is on')
print('verified flags: 41 live (30 original, 11 promoted 2026-10-11), every room-d unverified')
