"""bose-lifestyle: bridge-host command line. Read commands (status, config show, events) and change commands.

Live: the bridge's unix socket, with the same frames and validators as bose_request. Changes are the bridge's
manage op, checked locally with bose_ir.validate_management before anything is sent.
Offline (bridge not running): the saved config through ConfigStore (the same commit path the bridge uses) and the
event log. Nothing here opens a serial port, restarts the service or hand-edits config.json. The only Bose
command it can transmit is an explicit `identify --yes` to a running bridge.
"""
import argparse
import json
import os
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Sequence

import bose_receivers
import bose_state
import bose_request
from bose_commands import LIVE_MESSAGES
from bose_config import ConfigExists, ConfigStore, CommitError, SOURCES, ZONES, create_default_config
from bose_events import EventLog
from bose_ir import IRException, KEYS, identifier, validate_management

SOCKET = bose_request.SOCKET
SETTINGS = ('zones', 'sources', 'unverified', 'ir')
CHANGE_COMMANDS = ('zone', 'source', 'experimental', 'volume-steps', 'ir', 'identify')
RECEIVER_COMMANDS = ('list', 'verify', 'select')
REMOTE_COMMANDS = ('learn', 'forget', 'remote', 'button')
MODEL = 'ir-remote'                 # the only remote model; the name is the nickname (same as HA's flow)
NAME_LIMIT = 40
LEARN_STEP_SECONDS = 60.0           # client-side bound per wait; the bridge itself times a capture out after 30 s
POLL_SECONDS = 0.35
CHECK_TRIES = 3                     # a ready-check button that keeps not matching is given up after this many presses
ACTION = {'volume-up': 'Volume up', 'volume-down': 'Volume down', 'mute': 'Mute'}
MESSAGES = {
    'state_ok': 'The saved remote state is fine. Remotes saved: {profiles}.',
    'state_missing': 'There is no saved remote state yet. The bridge creates it on its first start.',
    'state_damaged': 'The saved remote state is damaged ({reason}). The file was not changed. {identity}',
    'state_identity_yes': 'Its pairing identity can still be read, so a reset can keep Home Assistant paired.',
    'state_identity_no': 'Its pairing identity cannot be read. Restore a backup, or reset with --new-identity and pair Home Assistant again.',
    'state_options': 'Options: bose-lifestyle state restore --from BACKUP --yes | state reset --keep-identity --yes | state reset --new-identity --yes. Stop the bridge first.',
    'state_running': 'The bridge is running. Stop it first, then run this again. Nothing was changed.',
    'state_fine': 'The saved remote state is not damaged, so nothing was changed.',
    'state_need_yes': 'This replaces the damaged file (a copy is kept next to it). Run again with --yes to do it.',
    'state_done': 'Done. The damaged file was kept as {copy}. Start the bridge again.',
    'state_failed': 'Nothing was changed: {code}.',
    'usage': 'Usage: bose-lifestyle {status,fault clear,config show,init,events [--count N],zone,source,experimental,volume-steps,'
             'ir {target,list,verify,select,learn,remote,button,forget},state {check,restore,reset},identify --yes}. Run with --help for details.',
    'ir_row': '{path}  {name}',
    'ir_remote_row': '{name}  {rid}  remote {state}',
    'ir_button_row': '    {action}: {state}',
    'ir_button_none': 'not learned',
    'ir_button_learned': 'learned, {state}',
    'ir_remotes_none': 'No learned remotes yet. Learn one with: bose-lifestyle ir learn --name NAME',
    'ir_remotes_offline': 'Learned remotes are not shown: the bridge is not running.',
    'ir_scans': 'Scan codes seen: {codes}',
    'ir_verify_note': 'Verify only lists what the receiver sends. Learning maps the buttons: bose-lifestyle ir learn --name NAME',
    'ir_bad_id': 'That is not a remote ID. Run `bose-lifestyle ir list` to see the IDs. Nothing was changed.',
    'ir_needs_bridge': 'The bridge is not running, so nothing was changed. Start the bridge and try again.',
    'ir_unknown_remote': 'That remote is not on the bridge. Run `bose-lifestyle ir list` to see the IDs. Nothing was changed.',
    'ir_no_mapping': 'That button has no saved code. Learn the remote again before turning it on. Nothing was changed.',
    'ir_forgot': 'Removed that remote from the bridge. Its saved buttons are gone.',
    'learn_notice': 'Other remotes you have already set up stop working while you learn this one. They work again when you finish.',
    'learn_name_length': 'Enter a name from 1 to 40 characters, with no control characters. Nothing was changed.',
    'learn_point': 'Point the remote at the IR receiver. Press and release {action} three times, about one second apart.',
    'learn_point_more': 'Held-button repeats do not count. Learning sends no Bose commands. ({step} of {steps})',
    'learn_start': 'Press Enter to listen, or type s to skip this button: ',
    'learn_heard': 'Three matching presses verified.',
    'learn_shared': 'This code is shared with another remote; identical IR signals cannot identify the physical remote.',
    'learn_timeout_note': 'No complete set of three matching presses arrived within 30 seconds.',
    'learn_inconsistent_note': 'The presses produced different codes. Retry or skip.',
    'learn_timeout_error': 'No complete set of three matching presses arrived within 30 seconds. Nothing was saved.',
    'learn_keep': 'Keep this button? Press Enter to keep, r to retry, or s to skip: ',
    'learn_retry_skip': 'Type r to retry or s to skip: ',
    'learn_pick_again': 'Type Enter, r or s.',
    'learn_conflict': 'This code belongs to a different button. Retry or skip.',
    'learn_review': 'Save {name}? Saving disables every learned button.',
    'learn_mapping': '{action}: {state}',
    'learn_mapping_learned': 'Learned',
    'learn_mapping_skipped': 'Skipped',
    'learn_save_prompt': 'Save this remote? Type y to save, or press Enter to cancel: ',
    'learn_nothing_saved': 'Nothing was saved.',
    'learn_conflict_error': 'This code belongs to a different button. Nothing was saved.',
    'learn_canceled': 'Learning was canceled. Nothing was saved.',
    'learn_busy': 'Another learning session is active. Finish or cancel it, or wait five minutes. Nothing was saved.',
    'learn_remote_limit': 'The bridge supports up to 32 remotes. Nothing was saved.',
    'learn_expired': 'The learning session ran out of time after five minutes. Nothing was saved. Run the command again.',
    'learn_no_capture': 'Three matching presses have not been verified. Nothing was saved.',
    'learn_failed': 'Something went wrong while learning. Nothing was saved.',
    'learn_saved': 'Saved remote {name}. Its buttons start disabled.',
    'learn_enable': 'Turn on a button, then the remote: bose-lifestyle ir button {rid} {key} on, then bose-lifestyle ir remote {rid} on',
    'learn_check_intro': 'Ready to use: press each learned button once. Checking sends no Bose commands.',
    'learn_check_point': 'Press {action} once. ({step} of {steps})',
    'learn_check_ok': '[x] {action} confirmed',
    'learn_check_miss': 'That press did not match {action}. Press it again.',
    'learn_check_timeout': 'No press arrived within 30 seconds. Press {action} once.',
    'learn_check_gave_up': 'That button did not confirm after {tries} presses. The remote is saved and its buttons stay disabled.',
    'learn_check_stopped': 'The check was stopped. The remote is saved and its buttons stay disabled.',
    'learn_check_timed_out': 'The check timed out. The remote is saved and its buttons stay disabled.',
    'learn_ready': 'Ready to use. Every learned button responded.',
    'learn_scan_technical': 'Scan code: {scan}',
    'learn_mapping_technical': '{action}: code {code}',
    'ir_unsupported': 'Unsupported: {path} ({name}) has no stable /dev/input/by-id/ path, so the bridge cannot use it.',
    'ir_none': 'No key-reporting receiver with a stable /dev/input/by-id/ path was found.',
    'ir_codes': 'Key codes seen: {codes}',
    'ir_no_keys': 'Nothing was changed: no key presses seen in {seconds:g} seconds. Press each button once and try again.',
    'ir_gone': 'The receiver was unplugged or stopped responding. Nothing was changed.',
    'ir_not_found': 'That receiver path was not found. Run `bose-lifestyle ir list` to see the choices.',
    'ir_permission': "This user cannot read the receiver. Run as the bridge's user or ask the installer for help.",
    'ir_no_evdev': 'The key reader (evdev) is not installed on this bridge host, so receivers cannot be checked.',
    'ir_by_id': 'Only a receiver with a /dev/input/by-id/ path can be saved. Pick one from `bose-lifestyle ir list`.',
    'ir_not_keys': 'That device does not report key presses. Pick a receiver from `bose-lifestyle ir list`.',
    'ir_invalid': 'Nothing was changed: {error}.',
    'ir_save_failed': 'Nothing was changed. The receiver settings could not be saved.',
    'ir_cannot_save': "Nothing was changed. This user cannot save the receiver settings. Run as the bridge's user or ask the installer for help.",
    'ir_saved': 'Saved receiver {name} to {path}. The bridge uses it after its next start.',
    'ir_select_only': "This chooses the receiver only. It does not teach the remote's buttons.",
    'ir_select_next': 'Next: learn your remote with `bose-lifestyle ir learn --name NAME`',
    'not_running': 'The bridge is not running. This is the saved configuration.',
    'cant_reach': "Can't reach the bridge: run this as the bridge's user or ask the installer",
    'no_config': 'There is no valid saved configuration. Ask the person who installed this system for help.',
    'no_config_missing': 'No configuration yet. Run: bose-lifestyle init',
    'init_created': 'Created the starting configuration: every room is off and no source is connected.',
    'init_next': ('Turn on a room: bose-lifestyle zone a on\n'
                  'Say which inputs are connected: bose-lifestyle source aux on'),
    'init_restart': 'It applies when the bridge next starts. After that:',
    'init_exists': 'A configuration already exists. Nothing was changed.',
    'init_running': 'The bridge is running with a valid configuration. Nothing was changed.',
    'failed': 'Something went wrong. Nothing was changed.',
    'no_events': 'No events yet.',
    'bridge_running': 'Bridge: running ({mode} mode); serial port {serial}; fault: {fault}',
    'bridge_stopped': 'Bridge: not running (saved configuration)',
    'serial_open': 'open',
    'serial_closed': 'closed',
    'none': 'none',
    'on': 'on',
    'off': 'off',
    'last_transmission': 'Last command sent: {transmission}',
    'no_transmission': 'none recorded',
    'transmission_unavailable': 'unavailable (bridge stopped)',
    'unverified': 'Unverified commands allowed for: {zones}',
    'ir_target': 'IR target: zone {zone}',
    'volume_steps': 'Volume steps per press: {steps}',
    'saved': 'Saved.',
    'saved_offline': 'Saved. It applies when the bridge next starts.',
    'reply_ok': 'Saved.',
    'reply_invalid_request': "That change wasn't valid. Nothing was changed.",
    'reply_dry_run': 'The bridge is in test mode, so changes are not allowed.',
    'reply_not_saved': 'Nothing was changed. The settings could not be saved.',
    'reply_uncertain': "We couldn't confirm the save. Check `bose-lifestyle config show` before trying again; nothing was retried.",
    'reply_unsupported': 'This bridge does not support that change.',
    'reply_unknown': 'The bridge did not confirm the change. Check `bose-lifestyle config show` before trying again; nothing was retried.',
    'zone_d_refused': ('Room D commands are unverified and experimental. Run again with --yes-i-understand '
                       'to confirm. Nothing was changed.'),
    'experimental_warning': 'Warning: unverified commands may not do what the label says.',
    'ir_target_forgets_mute': 'The last-commanded mute state will be forgotten.',
    'identify_explain': ('identify sends one manual identify command to the Bose controller and shows its reply. '
                         'It changes no setting. Run `bose-lifestyle identify --yes` to send it.'),
    'identify_offline': 'The bridge is not running, so nothing was sent. Start the bridge and try again.',
    'identify_failed': 'The bridge did not identify the controller. Nothing was changed.',
}


class _UsageError(Exception):
    pass


class NotRunning(Exception):
    """Offline backend asked to change something: nothing was sent or saved."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _UsageError(message)


def _count(text):
    value = int(text) if text.isdigit() else 0
    if not 1 <= value <= 200:
        raise argparse.ArgumentTypeError('--count must be 1 to 200')
    return value


def _steps(text):
    from bose_config import MAX_VOLUME_STEPS
    value = int(text) if text.isdigit() else 0
    if not 1 <= value <= MAX_VOLUME_STEPS:
        raise argparse.ArgumentTypeError('steps must be 1 to %d' % MAX_VOLUME_STEPS)
    return value


def _key_code(text):
    if not text.isdigit() or int(text) > 0xffff:
        raise argparse.ArgumentTypeError('key codes are 0 to 65535')
    return int(text)


def _parser():
    parser = _Parser(prog='bose-lifestyle', description='Show and check the Bose Lifestyle controller on this bridge computer.')
    commands = parser.add_subparsers(dest='command')
    commands.add_parser('status', help='show zones, sources, the IR target and whether the bridge is running')
    fault = commands.add_parser('fault', help='clear the bridge fault latch')
    fault.add_subparsers(dest='fault_command').add_parser('clear', help='clear the in-memory latch; does not test the Bose unit')
    config = commands.add_parser('config', help='saved configuration')
    config.add_subparsers(dest='config_command').add_parser('show', help='print the saved configuration as JSON')
    commands.add_parser('init', help='create the starting configuration: every room off, no source connected')
    events = commands.add_parser('events', help='show recent bridge events')
    events.add_argument('--count', type=_count, default=20, help='how many events, 1 to 200 (default 20)')
    zone = commands.add_parser('zone', help='turn a room zone on or off')
    zone.add_argument('zone', choices=ZONES)
    zone.add_argument('state', choices=('on', 'off'))
    zone.add_argument('--yes-i-understand', action='store_true', help='required for zone d on')
    source = commands.add_parser('source', help='say whether a source jack is connected')
    source.add_argument('source', choices=SOURCES)
    source.add_argument('state', choices=('on', 'off'))
    experimental = commands.add_parser('experimental', help='allow unverified commands for a zone')
    experimental.add_argument('zone', choices=ZONES)
    experimental.add_argument('state', choices=('on', 'off'))
    experimental.add_argument('--yes-i-understand', action='store_true', help='required for experimental d on')
    volume = commands.add_parser('volume-steps', help='how many volume steps one volume up or down press sends (1 to 5)')
    volume.add_argument('steps', type=_steps)
    ir = commands.add_parser('ir', help='infrared target zone and the IR receiver')
    ir_commands = ir.add_subparsers(dest='ir_command')
    ir_commands.add_parser('target', help='set the zone the remote controls').add_argument('zone', choices=ZONES)
    ir_commands.add_parser('list', help='list receivers with a stable by-id path and the learned remotes')
    verify = ir_commands.add_parser('verify', help='listen for key presses from a receiver for 15 seconds')
    verify.add_argument('path')
    select = ir_commands.add_parser('select', help='choose the receiver device (does not teach buttons)')
    select.add_argument('path')
    for flag in ('--volume-up', '--volume-down', '--mute'):
        select.add_argument(flag, type=_key_code, required=True)
    learn = ir_commands.add_parser('learn', help='teach a remote its volume up, volume down and mute buttons')
    learn.add_argument('--name', required=True, help='remote nickname, 1 to 40 characters')
    learn.add_argument('--yes', action='store_true', help='no prompts: keep each verified button and save')
    learn.add_argument('--technical', action='store_true', help='also show raw scan codes')
    forget = ir_commands.add_parser('forget', help='remove a learned remote from the bridge')
    forget.add_argument('remote_id', help='the ID that ir list shows')
    remote = ir_commands.add_parser('remote', help='turn a learned remote on or off')
    remote.add_argument('remote_id')
    remote.add_argument('state', choices=('on', 'off'))
    button = ir_commands.add_parser('button', help='turn one learned button on or off')
    button.add_argument('remote_id')
    button.add_argument('key', choices=KEYS)
    button.add_argument('state', choices=('on', 'off'))
    state = commands.add_parser('state', help='check or recover the saved remote state (offline; the bridge must be stopped)')
    state_commands = state.add_subparsers(dest='state_command')
    state_commands.add_parser('check', help='say whether the saved remote state is readable (changes nothing)')
    restore = state_commands.add_parser('restore', help='replace a damaged state file with a backup that loads cleanly')
    restore.add_argument('--from', dest='source', required=True)
    restore.add_argument('--yes', action='store_true')
    reset = state_commands.add_parser('reset', help='start with no learned remotes; the damaged file is kept')
    mode = reset.add_mutually_exclusive_group(required=True)
    mode.add_argument('--keep-identity', action='store_true')
    mode.add_argument('--new-identity', action='store_true')
    reset.add_argument('--yes', action='store_true')
    identify = commands.add_parser('identify', help='send the manual identify command once (needs --yes)')
    identify.add_argument('--yes', action='store_true', help='confirm the one identify transmission')
    return parser


class Backend:
    def status(self) -> dict:
        raise NotImplementedError

    def events(self) -> list:
        raise NotImplementedError

    def manage(self, payload: dict) -> dict:
        raise NotImplementedError


class SocketBackend(Backend):
    def __init__(self, socket_path):
        self.socket_path = socket_path

    def status(self) -> dict:
        return json.loads(bose_request.request(self.socket_path, b'status\n'))

    def events(self) -> list:
        return json.loads(bose_request.request(self.socket_path, b'events\n'))['events']

    def manage(self, payload: dict) -> dict:
        frame = b'manage\n' + json.dumps(payload, separators=(',', ':')).encode('utf-8')
        return json.loads(bose_request.request(self.socket_path, frame))

    def identify(self) -> str:
        return bose_request.request(self.socket_path, b'identify\n')

    def clear_fault(self) -> str:
        return bose_request.request(self.socket_path, b'fault-clear\n')


class OfflineBackend(Backend):
    """Saved configuration. Changes go through the ConfigStore setters, the same commit path as the bridge."""

    def __init__(self, state_dir):
        self.state_dir = Path(state_dir)

    def status(self) -> dict:
        store = ConfigStore(self.state_dir / 'config.json')
        config = store.config if store.state == 'ok' else None
        result = {'mode': 'offline', 'config': store.state}
        for key in SETTINGS:
            result[key] = None if config is None else config[key]
        result['volume_steps'] = 1 if config is None else config.get('volume_steps', 1)
        return result

    def events(self) -> list:
        return EventLog(self.state_dir / 'events.jsonl').recent()

    def manage(self, payload: dict) -> dict:
        store = ConfigStore(self.state_dir / 'config.json')
        op = payload['op']
        try:
            if op == 'set_zone':
                store.set_zone(payload['zone'], payload['enabled'])
            elif op == 'set_source':
                store.set_source(payload['source'], payload['connected'])
            elif op == 'set_unverified':
                store.set_unverified(payload['zone'], payload['allow'])
            elif op == 'set_volume_steps':
                store.set_volume_steps(payload['steps'])
            else:
                store.set_ir_zone(payload['zone'])
        except CommitError as error:                 # one attempt; the kind decides the message, never a retry
            return {'ok': False, 'error': error.kind}
        return {'ok': True, 'result': {}}

    def identify(self):
        raise NotRunning(MESSAGES['identify_offline'])


def _settings(status):
    if status.get('mode') == 'offline':
        return ({**{key: status[key] for key in SETTINGS}, 'volume_steps': status.get('volume_steps', 1)}
                if status['config'] == 'ok' else None)
    return status.get('config')


def _print_status(status, out, missing):
    settings = _settings(status)
    bridge = status.get('bridge')
    if bridge is None:
        out.write(MESSAGES['bridge_stopped'] + '\n')
        out.write(MESSAGES['last_transmission'].format(transmission=MESSAGES['transmission_unavailable']) + '\n')
    else:
        out.write(MESSAGES['bridge_running'].format(
            mode=bridge['mode'], serial=MESSAGES['serial_open'] if bridge['serial_open'] else MESSAGES['serial_closed'],
            fault=bridge['fault'] or MESSAGES['none']) + '\n')
        transmission = bridge.get('last_transmission')
        value = (f"{transmission['action']} ({transmission['outcome']})" if transmission else MESSAGES['no_transmission'])
        out.write(MESSAGES['last_transmission'].format(transmission=value) + '\n')
    if settings is None:
        out.write(MESSAGES['no_config_missing' if missing else 'no_config'] + '\n')
        return
    unverified = ', '.join(z.upper() for z in 'abcd' if settings['unverified'][z]) or MESSAGES['none']
    out.write(MESSAGES['unverified'].format(zones=unverified) + '\n')
    out.write(MESSAGES['ir_target'].format(zone=settings['ir']['zone'].upper()) + '\n')
    out.write(MESSAGES['volume_steps'].format(steps=settings.get('volume_steps', 1)) + '\n')


def _print_config(status, out, missing):
    settings = _settings(status)
    if settings is None:
        out.write(MESSAGES['no_config_missing' if missing else 'no_config'] + '\n')
        return 1
    shown = {k: v for k, v in settings.items() if not (k == 'volume_steps' and v == 1)}   # the file only has it when not 1
    out.write(json.dumps(shown, indent=2, sort_keys=True) + '\n')
    return 0


def _print_events(events, out, count):
    if not events:
        out.write(MESSAGES['no_events'] + '\n')
    for event in events[-count:]:
        stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(event['t']))
        out.write(' '.join(part for part in (stamp, event['kind'], event['action'] or '', event['detail']) if part) + '\n')


def _config_missing(state_dir) -> bool:
    """True only when config.json is absent; an unreadable one is reported as invalid, never as missing."""
    try:
        os.lstat(os.path.join(str(state_dir), 'config.json'))
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _show(args, backend, out, state_dir):
    missing = _config_missing(state_dir)
    if args.command == 'status':
        _print_status(backend.status(), out, missing)
        return 0
    if args.command == 'config':
        return _print_config(backend.status(), out, missing)
    _print_events(backend.events(), out, args.count)
    return 0


def _check_saved_state(state_dir):
    """A saved config we cannot read is not 'no config': raise instead of reporting it as missing."""
    path = Path(state_dir)
    if path.exists() and not os.access(str(path), os.R_OK | os.X_OK):
        raise PermissionError(str(path))
    config = path / 'config.json'
    if config.exists() and not os.access(str(config), os.R_OK):
        raise PermissionError(str(config))


def _read(args, socket_path, state_dir, out, err):
    """Live when the bridge answers; otherwise the saved configuration with one line saying so."""
    try:
        return _show(args, SocketBackend(socket_path), out, state_dir)
    except (FileNotFoundError, ConnectionRefusedError):
        _check_saved_state(state_dir)
        err.write(MESSAGES['not_running'] + '\n')
        return _show(args, OfflineBackend(state_dir), out, state_dir)


def _payload(args):
    """The manage op for one change; field names are the bridge's (bose_ir.validate_management)."""
    if args.command == 'zone':
        return {'op': 'set_zone', 'zone': args.zone, 'enabled': args.state == 'on'}
    if args.command == 'source':
        return {'op': 'set_source', 'source': args.source, 'connected': args.state == 'on'}
    if args.command == 'experimental':
        return {'op': 'set_unverified', 'zone': args.zone, 'allow': args.state == 'on'}
    if args.command == 'volume-steps':
        return {'op': 'set_volume_steps', 'steps': args.steps}
    return {'op': 'set_ir_zone', 'zone': args.zone}


def _needs_flag(args):
    return args.command in ('zone', 'experimental') and args.zone == 'd' and args.state == 'on' \
        and not args.yes_i_understand


def _report_change(result, args, offline, out, err):
    if not result.get('ok'):
        error = result.get('error')
        known = error in ('invalid_request', 'dry_run', 'not_saved', 'uncertain', 'unsupported')
        err.write(MESSAGES['reply_' + error if known else 'reply_unknown'] + '\n')
        return 1
    out.write((MESSAGES['saved_offline'] if offline else MESSAGES['saved']) + '\n')
    if args.command == 'ir':
        out.write(MESSAGES['ir_target_forgets_mute'] + '\n')
    return 0


def _identify(args, socket_path, out, err):
    if not args.yes:
        err.write(MESSAGES['identify_explain'] + '\n')
        return 2
    try:
        reply = SocketBackend(socket_path).identify()
    except (FileNotFoundError, ConnectionRefusedError):
        err.write(MESSAGES['identify_offline'] + '\n')          # never offline: nothing is sent
        return 1
    if reply != LIVE_MESSAGES['identify']:
        err.write(MESSAGES['identify_failed'] + '\n')
        return 1
    out.write(reply + '\n')
    return 0


def _change(args, socket_path, state_dir, out, err):
    if args.command == 'identify':
        return _identify(args, socket_path, out, err)
    if args.command == 'ir' and args.ir_command is None:
        err.write(MESSAGES['usage'] + '\n')
        return 2
    if _needs_flag(args):                                       # Zone D is opt-in: refused before anything is sent
        err.write(MESSAGES['zone_d_refused'] + '\n')
        return 2
    if args.command == 'experimental' and args.state == 'on' and args.zone != 'd':
        err.write(MESSAGES['experimental_warning'] + '\n')
    payload = _payload(args)
    try:
        validate_management(payload)                            # the CLI never sends what the bridge would reject
    except IRException:
        err.write(MESSAGES['reply_invalid_request'] + '\n')
        return 2
    try:
        result = SocketBackend(socket_path).manage(payload)
        offline = False
    except (FileNotFoundError, ConnectionRefusedError):
        _check_saved_state(state_dir)
        result = OfflineBackend(state_dir).manage(payload)
        offline = True
    return _report_change(result, args, offline, out, err)


def _on_off_word(flag):
    return MESSAGES['on'] if flag else MESSAGES['off']


def _list_remotes(opts, out):
    """Learned remotes from the running bridge: names, IDs and button state. No codes are ever printed."""
    try:
        status = SocketBackend(opts['socket_path']).status()
    except (OSError, ValueError):
        out.write(MESSAGES['ir_remotes_offline'] + '\n')
        return
    profiles = (status.get('ir') or {}).get('profiles') or {}
    if not profiles:
        out.write(MESSAGES['ir_remotes_none'] + '\n')
        return
    for rid, profile in profiles.items():
        out.write(MESSAGES['ir_remote_row'].format(name=profile['name'], rid=rid, state=_on_off_word(profile['active'])) + '\n')
        for key in KEYS:
            mapping = profile['mappings'][key]
            if mapping['code'] is None:
                state = MESSAGES['ir_button_none']
            else:
                state = MESSAGES['ir_button_learned'].format(state=_on_off_word(mapping['enabled']))
            out.write(MESSAGES['ir_button_row'].format(action=ACTION[key], state=state) + '\n')


class _Refused(Exception):
    """The bridge answered ok false with this error code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class _Cancel(Exception):
    """The person stopped (Ctrl-C or end of input)."""


class _Timeout(Exception):
    """A wait ran past LEARN_STEP_SECONDS on this side."""


class _Failed(Exception):
    """A non-interactive run hit a button it cannot accept; the text says why."""

    def __init__(self, text):
        super().__init__(text)
        self.text = text


KEEP_OPTIONS = {'k': 'keep', 'keep': 'keep', 'r': 'retry', 'retry': 'retry', 's': 'skip', 'skip': 'skip'}
RETRY_SKIP = {'r': 'retry', 'retry': 'retry', 's': 'skip', 'skip': 'skip'}
PHASE_NOTES = {'timeout': 'learn_timeout_note', 'conflict': 'learn_conflict', 'inconsistent': 'learn_inconsistent_note'}
REFUSAL_TEXT = {'busy': 'learn_busy', 'remote_limit': 'learn_remote_limit', 'no_capture': 'learn_no_capture',
                'session_expired': 'learn_expired', 'conflict': 'learn_conflict_error', 'unknown_remote': 'ir_unknown_remote',
                'no_mapping': 'ir_no_mapping', 'invalid_request': 'learn_failed'}


class _Learner:
    """ir learn: the same manage ops, in the same order and with the same step semantics, as HA's RemoteFlow.

    Learning: begin, then per button capture, session (poll until the phase leaves waiting), then keep, retry or
    skip; then review, commit. Ready check: begin with the committed remote_id, then per learned button capture,
    session, skip, and cancel at the end. Nothing here sends a Bose command.
    """

    def __init__(self, backend, out, ask, sleep, clock, yes, technical):
        self.backend, self.out, self.ask, self.sleep, self.clock = backend, out, ask, sleep, clock
        self.yes, self.technical = yes, technical
        self.sid = None
        self.rid = None

    def say(self, text):
        self.out.write(text + '\n')

    def call(self, payload):
        validate_management(payload)
        reply = self.backend.manage(payload)
        if not reply.get('ok'):
            raise _Refused(str(reply.get('error')))
        return reply.get('result', {})

    def prompt(self, text):
        try:
            return self.ask(text)
        except EOFError:
            raise _Cancel()

    def wait(self, done, sid):
        deadline = self.clock() + LEARN_STEP_SECONDS
        while True:
            state = self.call({'op': 'session', 'session_id': sid})
            if done(state):
                return state
            if self.clock() >= deadline:
                raise _Timeout()
            self.sleep(POLL_SECONDS)

    def pick(self, text, options, default=None):
        while True:
            answer = self.prompt(text).strip().lower()
            if not answer and default:
                return default
            if answer in options:
                return options[answer]
            self.say(MESSAGES['learn_pick_again'])

    def cancel(self):
        if self.sid is not None:
            sid, self.sid = self.sid, None
            with suppress(Exception):     # best effort: a failed cancel must not hide why learning stopped
                self.call({'op': 'cancel', 'session_id': sid})

    def run(self, name):
        self.say(MESSAGES['learn_notice'])
        state = self.call({'op': 'begin', 'name': name, 'model': MODEL})
        self.sid = state['session_id']
        while state['phase'] != 'review':
            state = self.button(state)
        self.say(MESSAGES['learn_review'].format(name=name))
        for key in KEYS:
            mapping = state['mappings'][key]
            label = MESSAGES['learn_mapping_learned'] if mapping['code'] is not None else MESSAGES['learn_mapping_skipped']
            self.say(MESSAGES['learn_mapping'].format(action=ACTION[key], state=label))
            if self.technical and mapping['code'] is not None:
                self.say(MESSAGES['learn_mapping_technical'].format(action=ACTION[key], code=mapping['code']))
        if not self.yes and self.prompt(MESSAGES['learn_save_prompt']).strip().lower() not in ('y', 'yes'):
            self.cancel()
            self.say(MESSAGES['learn_nothing_saved'])
            return 1
        result = self.call({'op': 'commit', 'session_id': self.sid})
        self.sid = None
        self.rid = result['remote_id']
        self.say(MESSAGES['learn_saved'].format(name=name))
        learned = [key for key in KEYS if state['mappings'][key]['code'] is not None]
        if not learned:
            return 0
        self.say(MESSAGES['learn_enable'].format(rid=self.rid, key=learned[0]))
        return self.check(name, learned, {key: state['mappings'][key]['code'] for key in learned})

    def button(self, state):
        """One button: HA's prepare, detected and wait steps. Returns the state for the next button or review."""
        self.say(MESSAGES['learn_point'].format(action=ACTION[state['key']]))
        self.say(MESSAGES['learn_point_more'].format(step=state['step'], steps=state['steps']))
        if not self.yes and self.prompt(MESSAGES['learn_start']).strip().lower() in ('s', 'skip'):
            return self.call({'op': 'choose', 'session_id': self.sid, 'choice': 'skip'})
        self.call({'op': 'capture', 'session_id': self.sid})
        state = self.wait(lambda s: s['phase'] != 'waiting', self.sid)
        while True:
            if state['phase'] == 'captured':
                if self.technical:
                    self.say(MESSAGES['learn_scan_technical'].format(scan=state['candidate']))
                self.say(MESSAGES['learn_heard'])
                if state['shared']:
                    self.say(MESSAGES['learn_shared'])
                choice = 'keep' if self.yes else self.pick(MESSAGES['learn_keep'], KEEP_OPTIONS, default='keep')
            else:
                note = MESSAGES.get(PHASE_NOTES.get(state['phase'], ''), MESSAGES['learn_failed'])
                if self.yes:
                    raise _Failed(note + ' ' + MESSAGES['learn_nothing_saved'])
                self.say(note)
                choice = self.pick(MESSAGES['learn_retry_skip'], RETRY_SKIP)
            if choice == 'retry':
                self.call({'op': 'choose', 'session_id': self.sid, 'choice': 'retry'})
                state = self.wait(lambda s: s['phase'] != 'waiting', self.sid)
                continue
            if choice == 'skip':
                return self.call({'op': 'choose', 'session_id': self.sid, 'choice': 'skip'})
            try:
                return self.call({'op': 'choose', 'session_id': self.sid, 'choice': 'keep'})
            except _Refused as refused:
                if refused.code != 'conflict':
                    raise
                if self.yes:
                    raise _Failed(MESSAGES['learn_conflict_error'])
                self.say(MESSAGES['learn_conflict'])

    def check(self, name, learned, codes):
        state = self.call({'op': 'begin', 'name': name, 'model': MODEL, 'remote_id': self.rid, 'keys': learned})
        sid = self.sid = state['session_id']
        self.say(MESSAGES['learn_check_intro'])
        tries = {}
        while True:
            key = state['key']
            self.say(MESSAGES['learn_check_point'].format(action=ACTION[key], step=state['step'], steps=state['steps']))
            self.call({'op': 'capture', 'session_id': sid})
            state = self.wait(lambda s: s['verified'] >= 1 or s['phase'] != 'waiting', sid)
            if state['verified'] >= 1 and state['candidate'] == codes[key]:
                self.say(MESSAGES['learn_check_ok'].format(action=ACTION[key]))
                state = self.call({'op': 'choose', 'session_id': sid, 'choice': 'skip'})
                if state['phase'] == 'review':
                    self.cancel()
                    self.say(MESSAGES['learn_ready'])
                    return 0
                continue
            tries[key] = tries.get(key, 0) + 1
            if state['phase'] == 'timeout':
                self.say(MESSAGES['learn_check_timeout'].format(action=ACTION[key]))
            else:
                self.say(MESSAGES['learn_check_miss'].format(action=ACTION[key]))
            if tries[key] >= CHECK_TRIES:
                self.cancel()
                self.say(MESSAGES['learn_check_gave_up'].format(tries=CHECK_TRIES))
                return 1


def _learn(args, opts, out, err):
    name = args.name.strip()
    try:
        if not 1 <= len(name) <= NAME_LIMIT:
            raise IRException('name length')
        validate_management({'op': 'begin', 'name': name, 'model': MODEL})   # the same name rule the bridge applies
    except IRException:
        err.write(MESSAGES['learn_name_length'] + '\n')
        return 2
    learner = _Learner(SocketBackend(opts['socket_path']), out, opts['ask'] or input, opts['sleep'], opts['clock'],
                       args.yes, args.technical)
    text = None
    try:
        return learner.run(name)
    except (_Cancel, KeyboardInterrupt):
        text = 'learn_canceled'
    except _Timeout:
        text = 'learn_timeout_error'
    except _Failed as failed:
        text = failed.text
    except _Refused as refused:
        text = MESSAGES[REFUSAL_TEXT.get(refused.code, 'learn_failed')]
    except (FileNotFoundError, ConnectionRefusedError):
        text = 'ir_needs_bridge'
    except OSError:
        text = 'cant_reach'
    except ValueError:                       # a reply that is not JSON, such as the bridge's plain rejected
        text = 'learn_failed'
    finally:
        learner.cancel()                     # never leave a session open for the five-minute expiry
    # only the failure paths reach here; a saved remote is always named as saved
    if learner.rid:
        err.write(MESSAGES['learn_check_stopped'] + '\n')
    else:
        err.write(MESSAGES.get(text, text) + '\n')
    return 1


def _remote_command(args, opts, out, err):
    """ir learn | forget | remote | button: manage ops on a running bridge only. Nothing is saved offline."""
    if args.ir_command == 'learn':
        return _learn(args, opts, out, err)
    if args.ir_command == 'forget':
        payload = {'op': 'delete', 'remote_id': args.remote_id}
    elif args.ir_command == 'remote':
        payload = {'op': 'update', 'remote_id': args.remote_id, 'active': args.state == 'on'}
    else:
        payload = {'op': 'update', 'remote_id': args.remote_id, 'key': args.key, 'enabled': args.state == 'on'}
    if not identifier(args.remote_id):
        err.write(MESSAGES['ir_bad_id'] + '\n')
        return 2
    try:
        validate_management(payload)
    except IRException:
        err.write(MESSAGES['reply_invalid_request'] + '\n')
        return 2
    try:
        reply = SocketBackend(opts['socket_path']).manage(payload)
    except (FileNotFoundError, ConnectionRefusedError):
        err.write(MESSAGES['ir_needs_bridge'] + '\n')
        return 1
    if not reply.get('ok'):
        known = {'busy': MESSAGES['learn_busy'], 'unknown_remote': MESSAGES['ir_unknown_remote'],
                 'no_mapping': MESSAGES['ir_no_mapping']}
        err.write(known.get(reply.get('error'), MESSAGES['reply_unknown']) + '\n')
        return 1
    out.write((MESSAGES['ir_forgot'] if args.ir_command == 'forget' else MESSAGES['saved']) + '\n')
    return 0


def _init(socket_path, state_dir, out, err):
    """Create the starting config only when none exists. A running bridge that already has a valid config is left alone."""
    live = False
    try:
        status = SocketBackend(socket_path).status()
    except (FileNotFoundError, ConnectionRefusedError):
        pass                                                     # no bridge: the saved file is all there is
    except OSError:
        err.write(MESSAGES['cant_reach'] + '\n')
        return 1
    else:
        if (status.get('bridge') or {}).get('config') == 'ok':
            out.write(MESSAGES['init_running'] + '\n')
            return 0
        live = True
    try:
        create_default_config(Path(state_dir) / 'config.json')
    except ConfigExists:
        err.write(MESSAGES['init_exists'] + '\n')
        return 1
    except CommitError as error:
        err.write(MESSAGES['reply_' + error.kind] + '\n')
        return 1
    out.write(MESSAGES['init_created'] + '\n')
    if live:                                                     # the running bridge loaded its config once: restart first
        out.write(MESSAGES['init_restart'] + '\n')
    out.write(MESSAGES['init_next'] + '\n')
    return 0


def _state(args, socket_path, state_dir, out, err):
    """state check|restore|reset. Offline only: a running bridge holds the file open and may rewrite it."""
    path = Path(state_dir) / 'ir-state.json'
    if args.state_command == 'check':
        info = bose_state.inspect(path)
        if not info['exists']:
            out.write(MESSAGES['state_missing'] + '\n')
            return 0
        if info['ok']:
            out.write(MESSAGES['state_ok'].format(profiles=info['profiles']) + '\n')
            return 0
        identity = MESSAGES['state_identity_yes' if info['identity_recoverable'] else 'state_identity_no']
        err.write(MESSAGES['state_damaged'].format(reason=info['reason'], identity=identity) + '\n')
        err.write(MESSAGES['state_options'] + '\n')
        return 1
    try:
        SocketBackend(socket_path).status()
    except (FileNotFoundError, ConnectionRefusedError):
        pass
    except OSError:
        err.write(MESSAGES['cant_reach'] + '\n')
        return 1
    else:
        err.write(MESSAGES['state_running'] + '\n')
        return 1
    if not args.yes:
        err.write(MESSAGES['state_need_yes'] + '\n')
        return 2
    try:
        if args.state_command == 'restore':
            copy = bose_state.restore(path, args.source)
        else:
            copy = bose_state.reset(path, keep_identity=args.keep_identity)
    except bose_state.StateRecoveryError as error:
        known = {'state-is-fine': 'state_fine'}
        text = MESSAGES[known[error.code]] if error.code in known else MESSAGES['state_failed'].format(code=error.code)
        err.write(text + '\n')
        return 1
    out.write(MESSAGES['state_done'].format(copy=copy) + '\n')
    return 0


def _receiver(args, opts, out, err):
    """ir list|verify|select: local receiver work only. Nothing here reaches the bridge socket or the Bose controller."""
    try:
        if args.ir_command == 'list':
            found = bose_receivers.list_receivers(opts['by_id_dir'], opts['opener'], opts['input_dir'])
            for receiver in found:
                out.write(MESSAGES['ir_row'].format(path=receiver.path, name=receiver.name) + '\n')
            for device in bose_receivers.list_unsupported(opts['input_dir'], opts['by_id_dir'], opts['opener']):
                out.write(MESSAGES['ir_unsupported'].format(path=device.path, name=device.name) + '\n')
            if not found:
                out.write(MESSAGES['ir_none'] + '\n')
            _list_remotes(opts, out)
            return 0
        if args.ir_command == 'verify':
            scans = []
            codes = bose_receivers.verify_receiver(args.path, opener=opts['opener'], clock=opts['clock'], scans=scans)
            if not codes and not scans:
                err.write(MESSAGES['ir_no_keys'].format(seconds=bose_receivers.VERIFY_SECONDS) + '\n')
                return 1
            if codes:
                out.write(MESSAGES['ir_codes'].format(codes=', '.join(str(code) for code in dict.fromkeys(codes))) + '\n')
            if scans:
                out.write(MESSAGES['ir_scans'].format(codes=', '.join(str(scan) for scan in dict.fromkeys(scans))) + '\n')
            out.write(MESSAGES['ir_verify_note'] + '\n')
            return 0
        if not args.path.startswith(bose_receivers.BY_ID + '/'):
            err.write(MESSAGES['ir_by_id'] + '\n')
            return 2
        receiver = bose_receivers.describe_receiver(args.path, opts['opener'])
        if not receiver.reports_keys:
            err.write(MESSAGES['ir_not_keys'] + '\n')
            return 2
        keys = {'volume-up': args.volume_up, 'volume-down': args.volume_down, 'mute': args.mute}
        try:
            bose_receivers.write_ir_config(Path(opts['ir_config']), receiver, keys)
        except PermissionError:
            err.write(MESSAGES['ir_cannot_save'] + '\n')
            return 1
        out.write(MESSAGES['ir_saved'].format(name=receiver.name, path=opts['ir_config']) + '\n')
        out.write(MESSAGES['ir_select_only'] + '\n' + MESSAGES['ir_select_next'] + '\n')
        return 0
    except bose_receivers.ReceiverGone:
        err.write(MESSAGES['ir_gone'] + '\n')
        return 1
    except ImportError:
        err.write(MESSAGES['ir_no_evdev'] + '\n')
        return 1
    except FileNotFoundError:
        err.write(MESSAGES['ir_not_found'] + '\n')
        return 1
    except PermissionError:
        err.write(MESSAGES['ir_permission'] + '\n')
        return 1
    except ValueError as error:
        err.write(MESSAGES['ir_invalid'].format(error=error) + '\n')
        return 2
    except OSError:
        err.write(MESSAGES['ir_save_failed'] + '\n')
        return 1


def main(argv: Sequence[str], *, socket_path: str = SOCKET, state_dir: str = '/var/lib/bose-control',
         out=None, err=None, ir_config: str = None, by_id_dir: str = bose_receivers.BY_ID,
         input_dir: str = bose_receivers.INPUT_DIR, opener=None, clock=time.monotonic,
         ask=None, sleep=time.sleep) -> int:
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    try:
        args = _parser().parse_args(list(argv))
    except _UsageError:
        err.write(MESSAGES['usage'] + '\n')
        return 2
    except SystemExit as exit_request:          # --help
        return exit_request.code if isinstance(exit_request.code, int) else 0
    if args.command is None or (args.command == 'config' and args.config_command is None) or (
            args.command == 'state' and args.state_command is None) or (args.command == 'fault' and args.fault_command is None):
        err.write(MESSAGES['usage'] + '\n')
        return 2
    opts = {'ir_config': ir_config or str(Path(state_dir) / 'ir-config.json'), 'by_id_dir': by_id_dir,
            'input_dir': input_dir, 'opener': opener, 'clock': clock, 'socket_path': socket_path,
            'ask': ask, 'sleep': sleep}
    try:
        if args.command == 'ir' and args.ir_command in RECEIVER_COMMANDS:
            return _receiver(args, opts, out, err)
        if args.command == 'ir' and args.ir_command in REMOTE_COMMANDS:
            return _remote_command(args, opts, out, err)
        if args.command == 'init':
            return _init(socket_path, state_dir, out, err)
        if args.command == 'state':
            return _state(args, socket_path, state_dir, out, err)
        if args.command == 'fault' and args.fault_command == 'clear':
            SocketBackend(socket_path).clear_fault()
            out.write('Bridge fault latch cleared. M1 communication was not tested.\n')
            return 0
        if args.command in CHANGE_COMMANDS:
            return _change(args, socket_path, state_dir, out, err)
        return _read(args, socket_path, state_dir, out, err)
    except NotRunning as error:
        err.write(str(error) + '\n')
        return 1
    except OSError:
        err.write(MESSAGES['cant_reach'] + '\n')
        return 1
    except Exception:                            # no traceback reaches the user
        err.write(MESSAGES['failed'] + '\n')
        return 1
