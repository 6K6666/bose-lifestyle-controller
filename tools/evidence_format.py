"""Format of promoted verification evidence, shared by the maintainer sanitizer, the generator and the tests.

ALLOWED_KEYS is the whole public (sanitized) record; maintainer/verify/sanitize_evidence.py drops everything else.
promotion_problems() checks VERIFIED_EVIDENCE against the verified names and the evidence files in docs/evidence/.
"""
import datetime
import hashlib
import json
import re
from pathlib import Path

from build_release import SHAPES_FILE, _lines  # the same generic shapes the archive scan uses

ALLOWED_KEYS = frozenset({'name', 'bytes_hex', 'expected_reply', 'reply', 'classification', 'outcome', 'timestamp',
                          'banner', 'observation', 'note', 'status_before', 'status_after', 'frames', 'frames_note'})
ENTRY_KEYS = frozenset({'evidence', 'sha256', 'banner', 'date'})
OUTCOMES = frozenset({'verified', 'reply_only'})
FREE_TEXT_FIELDS = ('note', 'observation', 'banner', 'reply')
FREE_TEXT_MAX = 200
# Home directories appear in free text only; the shared shapes file stays generic.
_EVIDENCE_SHAPES = [re.compile(s) for s in _lines(SHAPES_FILE)] + [re.compile(r'/(?:Users|home)/[^\s/]+')]


def _strings(value, path=''):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key in sorted(value):
            yield from _strings(value[key], f'{path}.{key}' if path else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _strings(item, f'{path}[{index}]')


def private_findings(record, literals=()):
    """Field paths of a record that carry a private shape or literal, or a free-text field that is too long or not
    printable ASCII. Names the field only: the text itself is never returned. literals are lowercase-compared."""
    found = []
    lowered = [lit.lower() for lit in literals]
    for path, text in _strings(record):
        if any(rx.search(text) for rx in _EVIDENCE_SHAPES) or any(lit in text.lower() for lit in lowered):
            found.append(f'{path}: private shape or literal')
        if path in FREE_TEXT_FIELDS and (len(text) > FREE_TEXT_MAX or not all(0x20 <= ord(c) <= 0x7e for c in text)):
            found.append(f'{path}: free text must be printable ASCII of 200 characters at most')
    return found


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def canonical(data):
    """The one byte form of a sanitized record: sorted keys, two-space indent, final newline."""
    return (json.dumps(data, indent=2, sort_keys=True) + '\n').encode('utf-8')


def promotion_problems(verified, table, original, evidence_dir, commands, expected, literals=()):
    """Every way a promotion can be inconsistent with its evidence. An empty list means the promotion is complete.

    commands maps name to command bytes; expected(command) gives the derived M1 reply frame (bytes).
    literals (optional) are private literals; the evidence text is checked against them, names only in the output.
    """
    problems = []
    for name in sorted(set(verified) - set(original)):
        if name not in table:
            problems.append(f'{name}: verified but has no VERIFIED_EVIDENCE entry')
    for name in sorted(table):
        entry = table[name]
        if name not in verified:
            problems.append(f'{name}: has evidence but is not verified')
        if name in original:
            problems.append(f'{name}: is in ORIGINAL_VERIFIED; original commissioning needs no entry')
        if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            problems.append(f'{name}: entry keys must be {sorted(ENTRY_KEYS)}')
            continue
        if not all(isinstance(entry[key], str) and entry[key].strip() for key in ENTRY_KEYS):
            problems.append(f'{name}: empty field in entry')
            continue
        try:
            datetime.datetime.strptime(entry['date'], '%Y-%m-%d')
        except ValueError:
            problems.append(f'{name}: date is not YYYY-MM-DD')
        evidence = entry['evidence']
        path = Path(evidence_dir) / evidence
        if '/' in evidence or not path.is_file():
            problems.append(f'{name}: evidence file missing: {evidence}')
            continue
        data = path.read_bytes()
        if sha256_bytes(data) != entry['sha256']:
            problems.append(f'{name}: sha256 does not match {evidence}')
            continue
        try:
            record = json.loads(data.decode('utf-8'))
        except ValueError:
            problems.append(f'{name}: evidence file is not JSON: {evidence}')
            continue
        problems.extend(f'{name}: evidence {finding}' for finding in private_findings(record, literals))
        if record.get('name') != name:
            problems.append(f'{name}: record name does not match the table key')
        if record.get('outcome') != 'verified':
            problems.append(f'{name}: record outcome is not verified (a reply_only record never promotes)')
        if record.get('banner') in (None, 'not checked') or record.get('banner') != entry['banner']:
            problems.append(f'{name}: record banner is not the table banner (identify-first banner, verbatim)')
        if record.get('timestamp') != entry['date']:
            problems.append(f'{name}: record date does not match the table date')
        if name not in commands:
            problems.append(f'{name}: not a catalogued command')
            continue
        frame = expected(commands[name])
        if record.get('bytes_hex') != commands[name].hex(' '):
            problems.append(f'{name}: record bytes do not match the command bytes')
        if record.get('expected_reply') != frame.decode('ascii'):
            problems.append(f'{name}: record expected reply is not the derived frame')
        frames = record.get('frames') or []
        if not any(f.get('kind') == 'rx' and f.get('detail') == frame.hex(' ') for f in frames):
            problems.append(f'{name}: no rx frame equal to the derived M1 reply (none recorded, or it differs)')
    return problems
