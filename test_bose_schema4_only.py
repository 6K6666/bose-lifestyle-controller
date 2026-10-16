"""One supported status schema (4): schema 2/3, set_room_live, rooms.json and legacy entity code are absent."""
import json
import re
from pathlib import Path
from runpy import run_path

from bose_ir import validate_status as ir_validate_status
from bose_request import validate_status as request_validate_status

ROOT = Path(__file__).resolve().parent
PROTOCOL = run_path(str(ROOT / 'custom_components/bose_lifestyle/protocol.py'))
SCHEMA4 = json.loads((ROOT / 'tests/fixtures/status_v4.json').read_text())
HA_FILES = sorted((ROOT / 'custom_components/bose_lifestyle').glob('*.py'))
HA_NO_PROTOCOL = [p for p in HA_FILES if p.name != 'protocol.py']
SKIP_NAMES = {'test_bose_schema4_only.py'}


def reply(schema, **edits):
    data = {**SCHEMA4, 'schema': schema, **edits}
    return json.dumps(data, separators=(',', ':'))


def repo_files(patterns):
    for pattern in patterns:
        for path in sorted(ROOT.glob(pattern)):
            if path.is_file() and not path.name.startswith('test_') and path.name not in SKIP_NAMES:
                yield path


def test_status_schema_3_rejected_cleanly():
    assert request_validate_status(reply(4))
    assert ir_validate_status(reply(4))['schema'] == 4
    assert PROTOCOL['validate_status'](reply(4))['schema'] == 4
    for schema in (2, 3, 5):
        text = reply(schema)
        for check, error in ((request_validate_status, OSError), (ir_validate_status, ValueError),
                             (PROTOCOL['validate_status'], ValueError)):
            try:
                check(text)
            except error:
                continue
            raise AssertionError(f'schema {schema} accepted by {check.__module__}')


def test_no_set_room_live_op_anywhere():
    patterns = ['bose_*.py', 'custom_components/bose_lifestyle/**/*.py',
                'custom_components/bose_lifestyle/**/*.js', 'custom_components/bose_lifestyle/**/*.json', 'tools/*.py']
    for path in repo_files(patterns):
        assert 'set_room_live' not in path.read_text(), path


def test_no_rooms_json_reads():
    for path in repo_files(['bose_*.py', 'custom_components/bose_lifestyle/**/*.py']):
        assert 'rooms.json' not in path.read_text(), path


def test_entities_come_only_from_actions():
    text = (ROOT / 'custom_components/bose_lifestyle/button.py').read_text()
    body = text[text.index('def wanted_buttons'):text.index('async def async_setup_entry')]
    assert "data['actions']" in body and 'SCHEMA3' not in text and 'LEGACY' not in text
    init = (ROOT / 'custom_components/bose_lifestyle/__init__.py').read_text()
    assert 'LEGACY' not in init and 'registry.entities' not in init and 'title' not in init.split('async def async_setup_entry')[1]


def test_no_schema_branches_in_ha():
    schema_branch = re.compile(r"schema\W{0,3}[23]\b|schema\s+in\s*\(\s*2|FROZEN_SCHEMA3|SCHEMA3_BUTTONS|room_d_live")
    for path in HA_NO_PROTOCOL:
        assert not schema_branch.search(path.read_text()), path
    for path in (ROOT / 'bose_ir.py', ROOT / 'custom_components/bose_lifestyle/protocol.py'):
        text = path.read_text()
        assert not re.search(r"data\[.schema.\]\s*in\s*\(\s*2|schema\s*==\s*3|schema\s*==\s*2|room_d_live", text), path
    js = (ROOT / 'custom_components/bose_lifestyle/frontend/bose-control-card.js').read_text()
    assert 'room_d_live' not in js and 'set_room_live' not in js


def test_protocol_is_byte_identical_to_bose_ir():
    assert (ROOT / 'bose_ir.py').read_bytes() == (ROOT / 'custom_components/bose_lifestyle/protocol.py').read_bytes()


if __name__ == '__main__':
    for name, value in list(globals().items()):
        if name.startswith('test_') and callable(value):
            value()
    print('Single supported status schema (4): schema 2/3, set_room_live, rooms.json and legacy entity code are absent')
