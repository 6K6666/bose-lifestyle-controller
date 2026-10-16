"""Status schema 4 between the bridge host and the HA client: schema 2/3 is rejected cleanly. Needs the HA test venv (run_tests.sh)."""
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from custom_components.bose_lifestyle.client import Client, BridgeError, validate_status
from custom_components.bose_lifestyle.button import Control, wanted_buttons
from custom_components.bose_lifestyle.commands import COMMANDS

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / 'tests' / 'fixtures'


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def record(action, outcome):
    return {'action': action, 'outcome': outcome, 'observed_at': time.time()}


class Stdin:
    def __init__(self): self.body = b''
    def write(self, body): self.body += body
    async def drain(self): pass
    def close(self): pass


class Process:
    def __init__(self, reply=b'', returncode=0):
        self.stdin = Stdin(); self.stdout = asyncio.StreamReader(); self.stderr = asyncio.StreamReader()
        self.stdout.feed_data(reply); self.stdout.feed_eof(); self.stderr.feed_eof(); self.returncode = returncode
    async def wait(self): return self.returncode
    def kill(self): self.returncode = -9


async def bridge_reply(action, stdout, returncode=0, client=None, payload=None):
    """Run one client call against a fake bridge host that answers with stdout; returns (result, argv list)."""
    client = client or Client({'host': 'bridge.example.invalid', 'user': 'bose', 'port': 22})
    spawned = []
    async def spawn(*args, **kwargs):
        spawned.append(args)
        return Process(stdout, returncode)
    with patch('asyncio.create_subprocess_exec', spawn):
        return await client.call(action, payload), spawned


ROOM_D_LIVE = {'op': 'set_room_live', 'room': 'd', 'live': True}   # removed op: the bridge host answers invalid_request


def test_ha_schema4_fixture_and_schema2_3_rejected():
    v4 = validate_status((FIXTURES / 'status_v4.json').read_text())
    assert v4['schema'] == 4 and v4['bridge']['config'] == 'ok' and 'room-a-source-fm' in v4['actions']
    for schema in (2, 3):
        data = fixture('status_v4.json')
        data['schema'] = schema
        try: validate_status(json.dumps(data))
        except OSError: pass
        else: raise AssertionError(f'HA accepted schema {schema}')


def test_status_schema_3_rejected_cleanly():
    """A schema-3 status fails validation: BridgeError('invalid_response') from the client and UpdateFailed in the coordinator (entities unavailable)."""
    from homeassistant.helpers.update_coordinator import UpdateFailed
    from custom_components.bose_lifestyle import Coordinator
    v3 = json.dumps({**fixture('status_v4.json'), 'schema': 3}).encode()
    client = Client({'host': 'bridge.example.invalid', 'user': 'bose', 'port': 22})
    try: asyncio.run(bridge_reply('status', v3, client=client))
    except BridgeError as error: assert str(error) == 'invalid_response', error
    else: raise AssertionError('schema 3 status accepted')
    coordinator = Coordinator.__new__(Coordinator)
    coordinator.client = Client({'host': 'bridge.example.invalid', 'user': 'bose', 'port': 22})
    coordinator.config_entry = SimpleNamespace(unique_id='b' * 32)
    coordinator.read_at, coordinator._error, coordinator._logged, coordinator._fault = 0, None, {}, None
    async def fail():
        with patch('asyncio.create_subprocess_exec', lambda *a, **k: _resolved(Process(v3))):
            return await coordinator._async_update_data()
    try: asyncio.run(fail())
    except UpdateFailed as error: assert 'invalid_response' in str(error), error
    else: raise AssertionError('coordinator accepted schema 3')


async def _resolved(value):
    return value


def test_unknown_health_value_degrades():
    data = fixture('status_v4.json')
    data['bridge'].update(fault='future-fault', recovery='future-recovery', config='future-config')
    health = validate_status(json.dumps(data))['bridge']
    assert (health['fault'], health['recovery'], health['config']) == ('unknown', 'unknown', 'unknown')


def test_actions_empty_after_uncertain_commit_does_not_make_ha_unavailable():
    data = fixture('status_v4.json')
    data['bridge'].update(config='config-uncertain', last_outcome=record('room-a-mute', 'uncertain'),
                          last_transmission=record('room-a-mute', 'uncertain'))
    data['config'], data['actions'] = None, {}
    checked = validate_status(json.dumps(data))
    assert checked['config'] is None and checked['actions'] == {}
    # The keys the entities and the coordinator read on every poll must all still be present.
    for key in ('mode', 'serial_open', 'fault', 'recovery', 'ir_attached'):
        assert key in checked['bridge'], key
    # Schema 4 with a non-empty catalog: identify is accepted, a name missing from actions is not.
    data = fixture('status_v4.json')
    data['bridge']['last_outcome'] = record('identify', 'dry-run')
    assert validate_status(json.dumps(data))['bridge']['last_outcome']['action'] == 'identify'
    data['bridge']['last_transmission'] = record('room-d-source-fm', 'transmitted-with-matching-response')
    data['actions'].pop('room-d-source-fm')
    try: validate_status(json.dumps(data))
    except OSError: pass
    else: raise AssertionError('schema 4 accepted a last_transmission outside actions')
    # Another invalid commit and a config-invalid bridge carry the same data.
    data['bridge']['config'], data['config'], data['actions'] = 'config-invalid', None, {}
    assert validate_status(json.dumps(data))['bridge']['config'] == 'config-invalid'


def test_unique_ids_unchanged_for_actions():
    """Every schema-4 action keeps the unique ID {bridge_id}_{key} and a button, except the excluded aliases."""
    data = fixture('status_v4.json')
    bridge_id = data['bridge_id']
    buttons = wanted_buttons(data)
    assert set(buttons) == set(data['actions']) - {'identify', 'system-power-on', 'system-power-off'}
    assert buttons['room-a-mute'] == 'Mute' and buttons['room-a-source-fm'] == 'FM'
    coordinator = SimpleNamespace(config_entry=SimpleNamespace(unique_id=bridge_id), data=data)
    for key, name in buttons.items():
        assert Control(coordinator, key, name).unique_id == f'{bridge_id}_{key}', key


def test_protocol_copy_in_sync():
    assert (ROOT / 'bose_ir.py').read_bytes() == (ROOT / 'custom_components' / 'bose_lifestyle' / 'protocol.py').read_bytes()


def test_schema4_bridge_new_ha_client():
    client = Client({'host': 'bridge.example.invalid', 'user': 'bose', 'port': 22})
    status, _ = asyncio.run(bridge_reply('status', (FIXTURES / 'status_v4.json').read_bytes(), client=client))
    assert status['schema'] == 4 and status['bridge']['config'] == 'ok'
    # The bridge host refuses the removed set_room_live op as an invalid request; HA reports it and sends nothing new.
    try: asyncio.run(bridge_reply('manage', b'{"ok":false,"error":"invalid_request"}', client=client, payload=ROOM_D_LIVE))
    except BridgeError as error: assert str(error) == 'invalid_request'
    else: raise AssertionError('invalid_request manage reply accepted')


if __name__ == '__main__':
    test_ha_schema4_fixture_and_schema2_3_rejected()
    test_status_schema_3_rejected_cleanly()
    test_unknown_health_value_degrades()
    test_actions_empty_after_uncertain_commit_does_not_make_ha_unavailable()
    test_unique_ids_unchanged_for_actions()
    test_protocol_copy_in_sync()
    test_schema4_bridge_new_ha_client()
    print('HA schema 4 only: schema 2/3 rejected cleanly, degrade, empty-actions, unique IDs and the removed op checks passed')
