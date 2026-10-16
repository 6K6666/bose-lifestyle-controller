"""Static HA integration checks that need no Home Assistant install."""
import json
from pathlib import Path

root = Path(__file__).resolve().parent
integ = root / 'custom_components' / 'bose_lifestyle'
assert (root / 'bose_ir.py').read_bytes() == (integ / 'protocol.py').read_bytes(), 'protocol.py must match bose_ir.py'
for path in integ.rglob('*.json'):
    json.loads(path.read_text(encoding='utf-8'))
for name in ('strings.json', 'translations/en.json', 'manifest.json'):
    json.loads((integ / name).read_text(encoding='utf-8'))
strings = json.loads((integ / 'strings.json').read_text(encoding='utf-8'))
remote = strings['config_subentries']['remotes']['step']
PAUSE = 'Other remotes you have already set up stop working while you learn this one. They work again when you finish.'
LEARN = ('Point {name} at the IR receiver. Select Listen, then press and release this button three times, about one second apart. '
         'Held-button repeats do not count. Learning sends no Bose commands.')


def test_learning_text_warns_other_remotes_pause():
    # The pause is explained on the name step (shown before the session starts) and again before learning.
    assert PAUSE in remote['name']['description'] and PAUSE in remote['prepare']['description']
    assert remote['prepare']['description'].endswith(LEARN) and LEARN in remote['prepare']['description']


def test_initial_pairing_screen_discloses_non_affiliation():
    description = strings['config']['step']['key']['description']
    assert 'Unofficial community project. Not affiliated with Bose Corporation.' in description


def test_hex_only_in_technical_details():
    flow_text = json.dumps(strings['config_subentries'])
    assert '0x' not in flow_text and '{code}' not in flow_text
    assert 'hex(' not in (integ / 'config_flow.py').read_text(encoding='utf-8')
    card = (integ / 'frontend' / 'bose-control-card.js').read_text(encoding='utf-8')
    details = card.find('class="tech"')
    assert all(i > details >= 0 for i in (m.start() for m in __import__('re').finditer('0x', card))), 'hex outside Technical details'


def test_bridge_unavailable_has_translation():
    assert strings['exceptions']['bridge_unavailable']['message'] == "Can't reach the bridge computer right now."


INVALID_RESPONSE = ("The bridge computer sent something this version can't read. "
                    "Check that the bridge and Home Assistant are on the same version.")


def test_invalid_response_has_translation_in_both_strings_files():
    assert strings['exceptions']['invalid_response']['message'] == INVALID_RESPONSE
    assert (integ / 'strings.json').read_bytes() == (integ / 'translations' / 'en.json').read_bytes()
    transport = (integ / 'transport.py').read_text(encoding='utf-8')
    assert "'invalid_response'" in transport.split('TRANSLATED_ERRORS', 1)[1].split('\n', 1)[0]


def test_ha_commands_has_no_unused_live_message_copy():
    source = (integ / 'commands.py').read_text(encoding='utf-8')
    assert 'LIVE_MESSAGES' not in source and 'D_ACTIONS' not in source and 'REFUSED_MESSAGE' in source


def test_receiver_sensor_unique_id_unchanged():
    source = (integ / 'binary_sensor.py').read_text(encoding='utf-8')
    assert "('receiver','IR receiver available')" in source
    assert "('serial','Serial available')" in source


def test_remote_flow_has_no_model_selector():
    source = (integ / 'config_flow.py').read_text(encoding='utf-8')
    assert "'model'" not in source and 'MODELS' not in source


def test_fault_notification_uses_fixed_p1_guidance():
    alert = (root / 'docs' / 'fault-notification.yaml').read_text(encoding='utf-8')
    expected = 'Home Assistant app controls are paused. Use the P1 remote.'
    assert expected in alert
    assert 'Bose remotes are paused' not in alert and 'Aaron' not in alert
    workspace_alert = root.parent / 'ha-bose-fault-alert.yaml'
    if workspace_alert.exists():
        installed = workspace_alert.read_text(encoding='utf-8')
        assert expected in installed
        assert 'Bose remotes are paused' not in installed and 'Aaron' not in installed


for test in (test_learning_text_warns_other_remotes_pause, test_initial_pairing_screen_discloses_non_affiliation,
             test_hex_only_in_technical_details, test_bridge_unavailable_has_translation,
             test_invalid_response_has_translation_in_both_strings_files, test_ha_commands_has_no_unused_live_message_copy,
             test_receiver_sensor_unique_id_unchanged, test_remote_flow_has_no_model_selector,
             test_fault_notification_uses_fixed_p1_guidance):
    test()
print('HA static checks passed')
