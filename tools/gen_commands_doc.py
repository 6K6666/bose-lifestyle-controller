#!/usr/bin/env python3
"""Generate docs/commands.md from the bridge catalog, the promotion table and docs/evidence/.

Usage: python3 tools/gen_commands_doc.py          (writes docs/commands.md)
       python3 tools/gen_commands_doc.py --check  (exit 1 if the committed file differs)
Output is deterministic: rows sorted by name, no dates of generation.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bose_commands import CATALOG, ORIGINAL_VERIFIED, VERIFIED_EVIDENCE  # noqa: E402

OUTPUT = ROOT / 'docs' / 'commands.md'
EVIDENCE = ROOT / 'docs' / 'evidence'
SOURCE_NAMES = {'am': 'AM', 'fm': 'FM', 'cd': 'CD', 'aux': 'Auxiliary', 'video-1': 'Video 1',
                'video-2': 'Video 2', 'tape': 'Tape'}
FUNCTION_NAMES = {'power-on': 'Power on', 'power-off': 'Power off', 'volume-up': 'Volume up',
                  'volume-down': 'Volume down', 'mute': 'Mute', 'unmute': 'Unmute'}
ROOM_NAMES = {'a': 'A', 'b': 'B', 'c': 'C', 'd': 'D'}


def reply_only_names(evidence_dir):
    """Commands with a sanitized reply_only record: the reply was captured but no effect was observed."""
    names = set()
    for path in sorted(Path(evidence_dir).glob('*.json')):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and record.get('outcome') == 'reply_only' and isinstance(record.get('name'), str):
            names.add(record['name'])
    return names


def _function(entry):
    if entry.function == 'source':
        return 'Source: ' + SOURCE_NAMES.get(entry.source, entry.source)
    return FUNCTION_NAMES[entry.function]


def _row(entry, table, original, reply_only):
    name = entry.name
    room = ROOM_NAMES[entry.zone]
    notes = []
    if entry.alias:
        notes.append('Alias (room A)')
    if entry.name in original:
        status, how = 'Verified', 'Original commissioning'
    elif name in table:
        item = table[name]
        status = 'Verified'
        how = f"Evidence `{item['evidence']}`, banner `{item['banner']}`, {item['date']}"
    else:
        how = 'Not verified'
        status = 'Experimental, reply captured only' if name in reply_only else 'Experimental'
        if name in reply_only:
            notes.append('Reply captured only; no observable effect recorded')
    if entry.zone == 'd':
        notes.append('Untested hardware')
    return (f'| `{name}` | {room} | {_function(entry)} | {status} | {how} | {"; ".join(notes) or "-"} |')


def render(catalog, table, original, evidence_dir):
    reply_only = reply_only_names(evidence_dir)
    rows = [_row(catalog[name], table, original, reply_only) for name in sorted(catalog)]
    verified = sum(1 for name in catalog if catalog[name].verified)
    lines = [
        '# Commands',
        '',
        'Generated from the bridge catalog by `tools/gen_commands_doc.py`. Edit the catalog and regenerate; do not edit this page.',
        '',
        f'{verified} commands are Verified and {len(catalog) - verified} are Experimental. A command is Verified only with '
        'evidence: original commissioning, or a promotion with a sanitized record in `docs/evidence/` (see '
        '[evidence](evidence/README.md)). Evidence is from one M1, at the banner shown.',
        '',
        'Experimental commands are sent only when their room allows unverified commands (`experimental` in the CLI). '
        'Experimental, reply captured only means the M1 replied with the exact frame, but no change was observed.',
        '',
        '**Room D is untested hardware.** There is no Room D unit to test against, so every Room D command is Experimental '
        'and ships opt-in.',
        '',
        '| Command | Room | Function | Status | Verification | Notes |',
        '| --- | --- | --- | --- | --- | --- |',
        *rows,
        '',
    ]
    return '\n'.join(lines)


def main(argv):
    parser = argparse.ArgumentParser(description='Generate docs/commands.md from the catalog.')
    parser.add_argument('--check', action='store_true', help='exit 1 if the committed file differs')
    args = parser.parse_args(argv)
    text = render(CATALOG, VERIFIED_EVIDENCE, ORIGINAL_VERIFIED, EVIDENCE)
    if args.check:
        current = OUTPUT.read_text(encoding='utf-8') if OUTPUT.exists() else None
        if current != text:
            print('docs/commands.md is out of date; run tools/gen_commands_doc.py')
            return 1
        return 0
    OUTPUT.write_text(text, encoding='utf-8')
    print('wrote ' + str(OUTPUT.relative_to(ROOT)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
