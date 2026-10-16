"""Guided pairing config flow and the bilingual strings. Run with the HA test venv: python test_bose_pairing_flow.py."""
import asyncio
import inspect
import base64
import contextlib
import json
import shutil
import struct
import tempfile
from pathlib import Path
from unittest.mock import patch
from homeassistant.core import HomeAssistant
from homeassistant import loader, bootstrap
from homeassistant.config_entries import ConfigEntries
from custom_components.bose_lifestyle import pairing
from custom_components.bose_lifestyle.client import Client, BridgeError

ROOT = Path(__file__).resolve().parent
COMPONENT = ROOT / 'custom_components' / 'bose_lifestyle'
BLOB = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + bytes(range(32))
KEY = base64.b64encode(BLOB).decode()
HOSTLINE = 'bridge.example.invalid ssh-ed25519 ' + KEY
FINGERPRINT = pairing.fingerprint_of(HOSTLINE)
HOST_DATA = {'host': 'bridge.example.invalid', 'user': 'bose', 'port': 22}
ERROR_KEYS = ['ssh_missing', 'ssh_keyscan_missing', 'key_unreadable', 'config_dir_unwritable', 'key_exists',
              'invalid_host', 'invalid_port', 'no_host_key', 'fingerprint_mismatch', 'cannot_connect', 'forced_command_refused']


@contextlib.asynccontextmanager
async def harness():
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(ROOT / 'custom_components', Path(tmp) / 'custom_components', ignore=shutil.ignore_patterns('__pycache__'))
        hass = HomeAssistant(tmp)
        loader.async_setup(hass)
        hass.config_entries = ConfigEntries(hass, {})
        await hass.config_entries.async_initialize()
        await bootstrap.async_load_base_functionality(hass)
        def unpatched(*args, **kwargs): raise AssertionError('test reached an unpatched transport or ssh-keyscan')
        with patch.object(Client, 'call', unpatched), patch.object(pairing, '_run_capped', unpatched):
            try:
                yield hass, Path(tmp)
            finally:
                await hass.async_stop()


def fake_status(bridge_id=None, error=None):
    async def call(self, action, payload=None):
        if error: raise BridgeError(error)
        return {'bridge_id': bridge_id, 'ir': {'profiles': {}}}
    return call


def no_env_problems(config_dir, which=None): return []


async def start(hass):
    return await hass.config_entries.flow.async_init('bose_lifestyle', context={'source': 'user'})


async def to_host(hass, flow):
    return await hass.config_entries.flow.async_configure(flow['flow_id'], {})


async def to_fingerprint(hass, flow, host=None):
    host = host or dict(HOST_DATA)
    return await hass.config_entries.flow.async_configure(flow['flow_id'], host)


def scan_patch(line=HOSTLINE):
    return patch.object(pairing, 'scan_host_key', lambda host, port, runner=None: line)


def known_hosts(config):
    return config / '.ssh' / pairing.KNOWN_HOSTS_NAME


def private_lines(config):
    text = (config / '.ssh' / pairing.KEY_NAME).read_text()
    return [line for line in text.splitlines() if line and not line.startswith('-----')]


async def test_flow_shows_pairing_command_with_generated_public_key():
    async with harness() as (hass, config):
        with patch.object(pairing, 'environment_problems', no_env_problems):
            flow = await start(hass)
            assert flow['type'] == 'form' and flow['step_id'] == 'key', flow
            public = pairing.existing_public_key(config)
            assert public is not None
            command = flow['description_placeholders']['command']
            assert command == f"sh install-bridge.sh --public-key '{public}' --user <user> --serial-device <path-to-serial-by-id>", command
            host = await to_host(hass, flow)
            assert host['type'] == 'form' and host['step_id'] == 'host', host
            seen = json.dumps([flow, host], default=str)
            for line in private_lines(config):
                assert line not in seen


async def test_flow_pins_host_key_only_after_confirm():
    async with harness() as (hass, config):
        with patch.object(pairing, 'environment_problems', no_env_problems), scan_patch():
            flow = await start(hass)
            flow = await to_host(hass, flow)
            fingerprint = await to_fingerprint(hass, flow)
            assert fingerprint['type'] == 'form' and fingerprint['step_id'] == 'fingerprint', fingerprint
            assert fingerprint['description_placeholders']['fingerprint'] == FINGERPRINT
            assert not known_hosts(config).exists()
            with patch.object(Client, 'call', fake_status('b' * 32)):
                done = await hass.config_entries.flow.async_configure(fingerprint['flow_id'], {'fingerprint': FINGERPRINT})
            assert done['type'] == 'create_entry', done
            assert known_hosts(config).read_text() == HOSTLINE + '\n'


async def test_flow_fingerprint_mismatch_aborts_and_pins_nothing():
    async with harness() as (hass, config):
        with patch.object(pairing, 'environment_problems', no_env_problems), scan_patch():
            flow = await start(hass)
            flow = await to_host(hass, flow)
            fingerprint = await to_fingerprint(hass, flow)
            with patch.object(Client, 'call', fake_status('c' * 32)):
                aborted = await hass.config_entries.flow.async_configure(fingerprint['flow_id'], {'fingerprint': 'SHA256:WRONG'})
            assert aborted['type'] == 'abort' and aborted['reason'] == 'fingerprint_mismatch', aborted
            assert not known_hosts(config).exists()


async def test_flow_ssh_missing_shows_fallback_text():
    async with harness() as (hass, config):
        with patch.object(pairing, 'environment_problems', lambda config_dir, which=None: ['ssh_missing', 'ssh_keyscan_missing']):
            flow = await start(hass)
        assert flow['type'] == 'abort' and flow['reason'] == 'ssh_missing', flow
        assert not (config / '.ssh').exists()
        strings = json.loads((COMPONENT / 'strings.json').read_text())
        assert strings['config']['abort']['ssh_missing'] == (
            "The ssh program isn't available in this Home Assistant installation, so it can't pair with the bridge computer. "
            "See 'Home Assistant without ssh' in the install guide."), strings['config']['abort']['ssh_missing']


async def test_flow_second_run_reuses_existing_key():
    async with harness() as (hass, config):
        with patch.object(pairing, 'environment_problems', no_env_problems):
            first = await start(hass)
            key_file = config / '.ssh' / pairing.KEY_NAME
            before = key_file.read_bytes()
            public = first['description_placeholders']['command']
            hass.config_entries.flow.async_abort(first['flow_id'])
            second = await start(hass)
            assert second['description_placeholders']['command'] == public
            assert key_file.read_bytes() == before


async def test_bridge_id_unique_id_unchanged():
    async with harness() as (hass, config):
        with patch.object(pairing, 'environment_problems', no_env_problems), scan_patch():
            flow = await start(hass)
            flow = await to_host(hass, flow)
            fingerprint = await to_fingerprint(hass, flow)
            with patch.object(Client, 'call', fake_status('d' * 32)):
                done = await hass.config_entries.flow.async_configure(fingerprint['flow_id'], {'fingerprint': FINGERPRINT})
            assert done['type'] == 'create_entry', done
            assert done['result'].unique_id == 'd' * 32
            assert done['result'].data == HOST_DATA
            flow = await start(hass)
            flow = await to_host(hass, flow)
            fingerprint = await to_fingerprint(hass, flow)
            with patch.object(Client, 'call', fake_status('d' * 32)):
                again = await hass.config_entries.flow.async_configure(fingerprint['flow_id'], {'fingerprint': FINGERPRINT})
            assert again['type'] == 'abort' and again['reason'] == 'already_configured', again


async def test_flow_cannot_connect_and_forced_command_refused_have_distinct_keys():
    async with harness() as (hass, config):
        with patch.object(pairing, 'environment_problems', no_env_problems), scan_patch():
            flow = await start(hass)
            flow = await to_host(hass, flow)
            fingerprint = await to_fingerprint(hass, flow)
            results = {}
            for code in ('cannot_connect', 'invalid_response'):
                with patch.object(Client, 'call', fake_status(error=code)):
                    shown = await hass.config_entries.flow.async_configure(fingerprint['flow_id'], {'fingerprint': FINGERPRINT})
                assert shown['type'] == 'form' and shown['step_id'] == 'fingerprint', shown
                results[code] = shown['errors']['base']
            assert results == {'cannot_connect': 'cannot_connect', 'invalid_response': 'forced_command_refused'}, results


async def test_flow_default_host_is_empty():
    async with harness() as (hass, config):
        with patch.object(pairing, 'environment_problems', no_env_problems):
            flow = await to_host(hass, await start(hass))
        defaults = {str(key): key.default for key in flow['data_schema'].schema}
        assert repr(defaults['host']) == '<undefined>', defaults   # Required without a default
        assert defaults['user']() == 'bose' and defaults['port']() == 22, defaults
        with patch.object(pairing, 'environment_problems', no_env_problems):
            bad = await hass.config_entries.flow.async_configure(flow['flow_id'], {'host': 'not a host!', 'user': 'bose', 'port': 22})
        assert bad['errors'] == {'host': 'invalid_host'}, bad
        with patch.object(pairing, 'environment_problems', no_env_problems):
            flow2 = await start(hass)
            flow2 = await to_host(hass, flow2)
            bad_port = await hass.config_entries.flow.async_configure(flow2['flow_id'], {'host': 'bridge.example.invalid', 'user': 'bose', 'port': 0})
        assert bad_port['errors'] == {'port': 'invalid_port'}, bad_port


async def test_strings_files_identical_and_cover_all_error_keys():
    first = (COMPONENT / 'strings.json').read_bytes()
    assert first == (COMPONENT / 'translations' / 'en.json').read_bytes()
    config = json.loads(first)['config']
    covered = set(config['error']) | set(config['abort'])
    missing = [key for key in ERROR_KEYS if key not in covered]
    assert not missing, missing
    text = json.dumps({k: config[k] for k in ('step', 'error', 'abort')}).lower()
    assert 'restart' not in text and 'unplug' not in text


async def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and inspect.iscoroutinefunction(v)]
    for test in tests:
        await test()
    print(f'HA pairing flow: {len(tests)} tests passed')


if __name__ == '__main__':
    asyncio.run(run())
