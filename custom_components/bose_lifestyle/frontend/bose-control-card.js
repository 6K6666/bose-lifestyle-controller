/* Bose Lifestyle Assistant card: iOS-style room cards, settings sheet and view strategy. One file, no dependencies, no build step. */
(function () {
  'use strict';

  const SUFFIX_LABELS = {
    'power-on': 'On', 'power-off': 'Off', 'volume-up': 'Volume up', 'volume-down': 'Volume down',
    mute: 'Mute', unmute: 'Unmute', 'source-video-1': 'TV', 'source-aux': 'Music', 'source-am': 'AM',
  };
  const KEY_LABELS = {
    'power-on': 'On', 'power-off': 'Off', 'volume-up': 'Volume up', 'volume-down': 'Volume down',
    mute: 'Mute', unmute: 'Unmute', tv: 'TV', music: 'Music',
    'source-am': 'AM', 'source-fm': 'FM', 'source-cd': 'CD', 'source-video-2': 'Video 2', 'source-tape': 'Tape',
  };
  const DEFAULT_SOURCE_LABELS = { am: 'AM', fm: 'FM', cd: 'CD', aux: 'AUX', 'video-1': 'Video 1', 'video-2': 'Video 2', tape: 'Tape' };
  // Exact wording for each failure class; these match strings.json "exceptions" (test_bose_transport.py checks both).
  const MESSAGES = {
    not_sent: "That wasn't sent. Nothing was changed.",
    uncertain: "We couldn't confirm that command. Check the room before pressing again.",
    not_allowed: 'That control is turned off.',
    connection_lost: "Can't reach the bridge computer right now.",
    fault_paused: 'FAULTED — controls paused; use the P1 remote',
    config_uncertain: "The bridge's settings couldn't be confirmed. Ask the person who installed this system for help.",
    refused_by_unit: 'The Bose control unit declined that command. Nothing was changed.',
    bridge_unavailable: "Bose control isn't available right now. Nothing was changed.",   // the press was never sent
  };
  // Setup saves (every setting, including nicknames): a definite refusal says nothing changed; anything else may have been saved, so it is uncertain. No retry control.
  const SETUP_MESSAGES = {
    not_saved: 'Nothing was changed. The settings could not be saved.',
    generic: "That change wasn't saved. Nothing was changed.",
    uncertain: "We couldn't confirm that change. Check the settings before changing them again.",
  };
  const EXPERIMENTAL_TEXT = 'Room D commands have never been tested on the Bose control unit. Turn this on only once a Room D zone is connected. The Bose control unit may refuse them, and an unexpected or missing reply stops all Bose control until someone investigates.';
  const DEFAULT_BRIDGE_NAME = 'Bose Controller';   // the bridge computer's name until an administrator sets a nickname
  const MAPPING_FALLBACK = 'Rooms A to D are the four room outputs on the Bose control unit.';
  const NOT_SET_UP = 'Not set up yet. Ask the person who installed this system.';
  const DEFINITE_REFUSALS = ['invalid_request', 'invalid_name', 'unsupported', 'not_ready', 'dry_run'];   // the bridge did not act on these
  const VOLUME_KEYS = new Set(['volume-up', 'volume-down']);   // pressed in quick succession on purpose: a dropped press is not an error
  // Volume repeats while held, at the bridge's own volume pacing (its fast interval is 250 ms). Mute is one press per tap.
  // Power and input changes need a longer gap: the bridge refuses a second non-volume command within a second, and
  // the receiver needs time to wake or switch before it takes the next one.
  const REPEAT_KEYS = new Set(['volume-up', 'volume-down']);
  const HOLD_START_MS = 400, HOLD_REPEAT_MS = 300;
  const SLOW_COOLDOWN_MS = 2500;
  const isSlowKey = key => key.startsWith('power-') || key.startsWith('source-') || key === 'tv' || key === 'music';
  const NOT_SENT_MS = 1500;   // a rate-limited control is disabled this long; nothing is queued or repeated
  // Plain words for why a schema-4 control is unavailable, and for why the IR remote cannot control its target room.
  const REASON_TEXT = {
    'zone-disabled': 'This room is turned off in settings',
    'source-not-connected': 'This input is not connected',
    'experimental-off': 'Experimental; not enabled for this room',
    fault: MESSAGES.fault_paused,
    'not-live': 'Not available right now',
  };
  const IR_REASON_TEXT = {
    'zone-disabled': 'Turn this room on in settings to use the remote with it.',
    'experimental-off': 'Volume control for this room is experimental and not enabled.',
    'config-invalid': 'Settings are invalid, so the remote is paused.',
    'config-uncertain': "The bridge's settings couldn't be confirmed, so the remote is paused.",
    fault: 'The remote is paused until the bridge is checked.',
    'not-live': 'The remote is not available right now.',
  };
  // The eleven built-in input icons (Task 10d): one table of inline SVG paths on a 24 px grid. Stored ids outside it fall back to the source default.
  const INPUT_ICONS = {
    tv: { label: 'TV', path: '<rect x="3" y="5" width="18" height="12" rx="2"/><path d="M8 21h8"/>' },
    music: { label: 'Music', path: '<path d="M9 18V6l10-2v12"/><circle cx="7" cy="18" r="2"/><circle cx="17" cy="16" r="2"/>' },
    radio: { label: 'Radio', path: '<rect x="3" y="8" width="18" height="12" rx="2"/><path d="M7 8l9-4"/><circle cx="15" cy="14" r="2.5"/><path d="M6 12h3"/>' },
    disc: { label: 'Disc', path: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.5"/>' },
    tape: { label: 'Tape', path: '<rect x="3" y="6" width="18" height="12" rx="2"/><circle cx="8.5" cy="12" r="2"/><circle cx="15.5" cy="12" r="2"/>' },
    game: { label: 'Game', path: '<path d="M7 9h10a4 4 0 0 1 4 4v1a3 3 0 0 1-5 2l-1-1h-4l-1 1a3 3 0 0 1-5-2v-1a4 4 0 0 1 4-4z"/><path d="M8 12v2M7 13h2"/>' },
    speaker: { label: 'Speaker', path: '<rect x="6" y="3" width="12" height="18" rx="2"/><circle cx="12" cy="14" r="3"/><path d="M10 7h4"/>' },
    computer: { label: 'Computer', path: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>' },
    phone: { label: 'Phone', path: '<rect x="7" y="3" width="10" height="18" rx="2"/><path d="M11 18h2"/>' },
    streaming: { label: 'Streaming', path: '<path d="M5 12a7 7 0 0 1 14 0"/><path d="M8 12a4 4 0 0 1 8 0"/><circle cx="12" cy="12" r="1.5"/>' },
    input: { label: 'Input', path: '<path d="M4 12h10"/><path d="M10 8l4 4-4 4"/><path d="M20 5v14"/>' },
  };
  // Default icon per source jack (the server resolves the same defaults).
  const DEFAULT_ICON = { 'video-1': 'tv', aux: 'input', am: 'radio', fm: 'radio', cd: 'disc', 'video-2': 'game', tape: 'tape' };
  const ICONS = {
    'power-on': '<path d="M12 3v8"/><path d="M6.3 6.3a8 8 0 1 0 11.4 0"/>',
    'power-off': '<path d="M12 3v8"/><path d="M6.3 6.3a8 8 0 1 0 11.4 0"/>',
    'volume-up': '<path d="M12 5v14M5 12h14"/>',
    'volume-down': '<path d="M5 12h14"/>',
    mute: '<path d="M4 9v6h4l5 4V5L8 9H4z"/><path d="M16 9l5 6M21 9l-5 6"/>',
    unmute: '<path d="M4 9v6h4l5 4V5L8 9H4z"/><path d="M16 9a4 4 0 0 1 0 6"/><path d="M18.5 6.5a8 8 0 0 1 0 11"/>',
    chevron: '<path d="M6 9l6 6 6-6"/>',
    gear: '<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
  };

  // Dismiss clears the notice or the sheet error it sits beside; a 44 px control with its own accessible name.
  function dismissControl() {
    return '<button class="ctl" data-act="dismiss" data-focus="dismiss" aria-label="Dismiss message">Dismiss</button>';
  }

  function esc(value) {
    return String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function icon(name) {
    const path = ICONS[name] || (INPUT_ICONS[name] && INPUT_ICONS[name].path) || '';
    return `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
  }

  function pickOpenRoom(rooms, stored) {
    if (!rooms.length) return null;
    return rooms.includes(stored) ? stored : rooms[0];
  }

  function relativeTime(seconds) {
    if (!(seconds >= 45)) return 'just now';
    if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min ago`;
    return `${Math.floor(seconds / 3600)} h ago`;
  }

  function actionLabel(action) {
    const suffix = action === 'volume-down' ? 'volume-down' : String(action).replace(/^room-[a-d]-/, '');
    return SUFFIX_LABELS[suffix] || suffix;
  }

  function ownsAction(room, action) {
    return action.startsWith(`room-${room}-`) || (room === 'a' && action === 'volume-down');
  }

  // Last-commanded text only: the bridge has no live state, so this never claims what the room is doing now.
  // A source press is named by the input's nickname (the same label its tile shows) when `inputs` is given.
  function lastSent(outcome, room, nowSec, name = 'Room D', inputs = null) {
    if (!outcome || typeof outcome.action !== 'string' || !ownsAction(room, outcome.action)) return null;
    if (outcome.outcome === 'refused-by-m1') return `${name}: the Bose control unit declined the last command`;
    if (outcome.outcome !== 'transmitted-with-matching-response') return null;
    const suffix = outcome.action.replace(/^room-[a-d]-/, '');
    const source = sourceOf(suffix);
    const label = inputs && source && inputs[source] ? inputs[source].name : actionLabel(outcome.action);
    return `Last sent: ${label} · ${relativeTime(nowSec - outcome.observed_at)}`;
  }

  function controlsReady(healthState) {
    return healthState === 'serial-open';
  }

  function telemetryLine(room, field, label, nowSec, format) {
    const sample = room && room[field];
    if (!sample || sample.supported !== true || sample.value === null) return { label, value: 'State unavailable' };
    const value = format(sample.value);
    if (value === null) return { label, value: 'State unavailable' };
    const age = typeof sample.observed_at === 'number' && Number.isFinite(sample.observed_at)
      ? nowSec - sample.observed_at : Infinity;
    return { label, value: `${value}${age > 15 ? ' · Last reported' : ''}` };
  }

  function irLine(target) {
    if (!target) return '';
    const head = target.zone_name ? `This remote controls ${esc(target.zone_name)}` : 'The remote has no target room';
    const why = target.allowed ? '' : `<div class="ir warn">${esc(IR_REASON_TEXT[target.reason] || 'The remote cannot control this room.')}</div>`;
    return `<div class="ir">${head}</div>${why}`;
  }

  // The accessible name carries the room and the function ("Room A, Volume up"); a not-sent flag is part of the name too.
  function control(key, spec, extra = '', roomName = '') {
    const label = KEY_LABELS[key];
    const dis = spec.disabled ? ' disabled' : '';
    const why = ` title="${esc(spec.reason && REASON_TEXT[spec.reason] ? REASON_TEXT[spec.reason] : label)}"`;
    const name = `${roomName ? `${roomName}, ` : ''}${label}${spec.flag ? `, ${spec.flag}` : ''}`;
    return `<button class="ctl ${extra}" data-key="${esc(key)}" data-entity="${esc(spec.entity)}" data-focus="ctl:${esc(spec.entity)}" aria-label="${esc(name)}"${why}${dis}>${icon(key)}<span>${esc(spec.flag || label)}</span></button>`;
  }

  // Layout key -> source id: tv is the Television (Video 1) jack, music is Auxiliary, source-X is X; anything else is not a source.
  function sourceOf(key) {
    if (key === 'tv') return 'video-1';
    if (key === 'music') return 'aux';
    return key.startsWith('source-') ? key.slice('source-'.length) : null;
  }

  // One source tile: its own rounded button, the icon above the nickname, and text markers for the last press or a refusal.
  function tile(key, spec, roomName, roomId, last) {
    const label = spec.label || KEY_LABELS[key];
    const iconId = INPUT_ICONS[spec.icon] ? spec.icon : (DEFAULT_ICON[sourceOf(key)] || 'input');
    const dis = spec.disabled ? ' disabled' : '';
    const why = ` title="${esc(spec.reason && REASON_TEXT[spec.reason] ? REASON_TEXT[spec.reason] : label)}"`;
    const tags = (last ? '<em class="tag">Last sent</em>' : '') + (spec.flag ? `<em class="tag">${esc(spec.flag)}</em>` : '');
    const name = [roomName, label, last ? 'Last sent' : null, spec.flag || null].filter(Boolean).join(', ');
    return `<button class="ctl tile" data-key="${esc(key)}" data-room="${esc(roomId)}" data-entity="${esc(spec.entity)}" data-focus="ctl:${esc(spec.entity)}" aria-label="${esc(name)}"${why}${dis}>`
      + `${icon(iconId)}<span>${esc(label)}</span>${tags}</button>`;
  }

  function renderRoom(model) {
    const b = Object.fromEntries(Object.entries(model.buttons).filter(([k]) => k in KEY_LABELS).map(([k, v]) => [k, { ...v, disabled: v.disabled || model.disabled }]));
    const has = k => k in b;
    const open = !!model.open;
    const caption = model.caption ? `<span class="cap">${esc(model.caption)}</span>` : '';
    let html = `<section class="room${open ? ' open' : ''}" data-room="${esc(model.id)}">`;
    html += `<div class="head"><button class="title" data-act="toggle" data-room="${esc(model.id)}" data-focus="toggle:${esc(model.id)}" aria-expanded="${open}">`
      + `<span class="name">${esc(model.name)}</span>${caption}</button>`;
    if (open && (has('power-on') || has('power-off'))) {
      html += '<div class="seg power">' + ['power-on', 'power-off'].filter(has).map(k => control(k, b[k], "", model.name)).join('') + '</div>';
    }
    html += `<span class="chev" aria-hidden="true">${icon('chevron')}</span></div>`;
    if (model.telemetry) {
      // Reported values, shown for every room even while its power is off; a selected input is not proof that audio is playing.
      html += '<dl class="telemetry">' + [model.telemetry.volume, model.telemetry.source]
        .map(line => `<dt>${esc(line.label)}</dt><dd>${esc(line.value)}</dd>`).join('') + '</dl>';
    }
    // Why a control is unavailable, as visible text under the room name (not only a title attribute).
    const reasons = [...new Set(Object.values(b).filter(v => v.reason && REASON_TEXT[v.reason]).map(v => REASON_TEXT[v.reason]))];
    html += reasons.map(text => `<div class="why">${esc(text)}</div>`).join('');
    if (open) {
      if (has('volume-up') || has('volume-down')) {
        html += '<div class="vol">' + ['volume-down', 'volume-up'].filter(has).map(k => control(k, b[k], 'icon-only big', model.name)).join('') + '</div>';
      }
      if (has('mute') || has('unmute')) {
        html += '<div class="row">' + ['mute', 'unmute'].filter(has).map(k => control(k, b[k], "", model.name)).join('') + '</div>';
      }
      const sources = ['tv', 'music', ...Object.keys(b).filter(k => k.startsWith('source-'))];
      if (sources.some(has)) {
        html += '<div class="seg src">' + sources.filter(has).map(k => tile(k, b[k], model.name, model.id, model.lastSent === k)).join('') + '</div>';
      }
    }
    return html + '</section>';
  }

  const CONFIRM_D = "Room D commands have never been tested on the Bose control unit. Only turn this on once a Room D zone is connected. The Bose control unit may refuse them, and an unexpected or missing reply stops all Bose control until someone investigates. Enable Room D?";
  const STORE_KEY = 'bose_lifestyle.open_room';
  const NOT_READY = "Bose Lifestyle Assistant isn't ready";
  const UNAVAILABLE = 'Sound controls are unavailable. Use the Bose remote.';

  function generateView() {
    return { title: 'Bose Lifestyle Assistant', cards: [{ type: 'custom:bose-control-card' }] };
  }

  function generateDashboard() {
    return { views: [{ title: 'Bose Lifestyle Assistant', path: 'bose', icon: 'mdi:speaker', cards: [generateView().cards[0]] }] };
  }

  const MANAGE_DEFAULT = '/config/integrations/integration/bose_lifestyle';
  const DISCLAIMER = 'Unofficial community project. Not affiliated with Bose Corporation.';

  // The room list for the normal card. Administrators on a fresh install get Finish setup; everyone else a neutral sentence.
  function renderMain(v) {
    let html = `<div class="app${v.dim ? ' dim' : ''}">`;
    if (v.admin) html += `<button class="gear" data-act="gear" data-focus="gear" aria-label="Settings">${icon('gear')}</button>`;
    html += `<div class="bhead"><span class="name">${esc(v.bridgeName || DEFAULT_BRIDGE_NAME)}</span></div>`;
    if (v.setupNeeded) {
      html += v.admin ? '<div class="setup"><button class="ctl warn" data-act="setup" data-focus="setup">Finish setup</button></div>'
        : `<div class="msg">${esc(NOT_SET_UP)}</div>`;
    }
    // The infrared target is a setting for the handheld remotes, shown in the admin settings only, not on the daily card.
    if (v.message) html += `<div class="msg">${esc(v.message)}</div>`;
    if (v.message === MESSAGES.fault_paused && v.faultMessage) html += `<div class="msg fault-detail">${esc(v.faultMessage)}</div>`;
    if (v.notice) html += `<div class="notice"><span>${esc(v.notice)}</span>${dismissControl()}</div>`;
    html += v.rooms.map(renderRoom).join('');
    html += `<p class="meta disclaimer">${DISCLAIMER}</p>`;
    return html + '</div>';
  }

  function shouldRender({ sheet, reason, html, last, typing }) {
    if (!sheet) return html !== last;
    if (reason !== 'hass') return true;   // the card's own change always shows
    return html !== last && !typing;      // a state update never rebuilds under someone who is typing
  }

  // One room row: the name field with its own label above it, then "Enable app control for this room" with its switch on the same line.
  function roomRow(r, locked) {
    const label = `Room ${String(r.id).toUpperCase()}`;
    const rid = esc(r.id);
    return `<div class="rrow room-row${locked ? ' locked' : ''}" data-room="${rid}">`
      + `<label class="rlabel field-label" for="room-name-${rid}">Name for ${esc(label)}</label>`
      + `<input id="room-name-${rid}" class="rname" data-act="name" data-room="${rid}" data-focus="name:${rid}" value="${esc(r.name)}" maxlength="40">`
      + `<span class="rlabel">Enable app control for this room</span>`
      + `<label class="sw"><input type="checkbox" data-act="enable" data-room="${rid}" data-focus="enable:${rid}" aria-label="Enable app control for this room, ${esc(label)}"${r.enabled ? ' checked' : ''}${locked ? ' disabled' : ''}><span></span></label>`
      + '</div>' + (locked ? '<p class="meta helper">Tick the box above to unlock this switch.</p>' : '');
  }

  // The Experimental gate: one bordered box with a drawn checkbox, the sentence it confirms, and the state once ticked.
  function ackGate(room, acked) {
    const rid = esc(room);
    return `<label class="gate"><input type="checkbox" class="cbox" data-act="ack" data-room="${rid}" data-focus="ack:${rid}"${acked ? ' checked' : ''}>`
      + `<span class="gate-text">I understand these commands are untested and may not work as labeled.</span>`
      + `<span class="gate-state">${acked ? 'On' : ''}</span></label>`;
  }

  // A labeled radio group of the eleven icons for one source; the selected one is checked.
  function iconPicker(source, label, selected) {
    const radios = Object.keys(INPUT_ICONS).map(id => `<label class="ico"><input type="radio" name="icon-${esc(source)}" value="${esc(id)}" data-act="input-icon" data-source="${esc(source)}" data-icon="${esc(id)}" data-focus="input-icon:${esc(source)}:${esc(id)}"${id === selected ? ' checked' : ''}>`
      + `<span class="ico-box">${icon(id)}</span><span class="ico-txt">${esc(INPUT_ICONS[id].label)}</span></label>`).join('');
    return `<fieldset class="icons"><legend class="meta">Icon for ${esc(label)}</legend><div class="ico-grid">${radios}</div></fieldset>`;
  }

  // The IR remote's settings: the receiver's label (HA-side only) and the room it controls. A change waits for Save; the warning shows first.
  function irSettings(m) {
    const current = m.irTarget && m.irTarget.zone ? m.irTarget.zone : '';
    const chosen = m.irPending || current;
    const options = (m.rooms || []).map(r => `<option value="${esc(r.id)}"${r.id === chosen ? ' selected' : ''}>${esc(r.name)}</option>`).join('');
    let html = '<div class="rrow ir-row"><label class="rlabel" for="ir-label">Where is the IR receiver?</label>'
      + `<input id="ir-label" class="rname" data-act="ir-label" data-focus="ir-label" value="${esc(m.irLabel || '')}" maxlength="40" placeholder="For example, Kitchen">`
      + '<div class="brow"><button class="ctl" data-act="ir-label-save" data-focus="ir-label-save">Save label</button></div>'
      + '<div class="meta">A label for your records only. Leave it blank to remove the label.</div></div>';
    html += '<div class="rrow ir-row"><label class="rlabel" for="ir-target">Which room does it control?</label>'
      + `<select id="ir-target" class="rname" data-act="ir-target" data-focus="ir-target"${current ? '' : ' disabled'}>${options}</select></div>`;
    if (m.irPending && m.irPending !== current) {
      html += '<div class="confirm"><p>The last-commanded mute state will be forgotten.</p><div class="row">'
        + '<button class="ctl" data-act="ir-cancel" data-focus="ir-cancel">Cancel</button><button class="ctl warn" data-act="ir-save" data-focus="ir-save">Save</button></div></div>';
    }
    return html;
  }

  // How many volume steps one volume up or volume down press sends (1 to 5). Applies to Rooms A to C, from the card, the
  // infrared remote and the command line alike; the bridge computer does the repeating, one checked step at a time.
  function volumeSettings(m) {
    const current = Number.isInteger(m.volumeSteps) ? m.volumeSteps : 1;
    const options = [1, 2, 3, 4, 5].map(n => `<option value="${n}"${n === current ? ' selected' : ''}>${n}</option>`).join('');
    return '<div class="dhead">Volume</div><div class="rrow vol-row"><label class="rlabel" for="volume-steps">Volume steps per press</label>'
      + `<select id="volume-steps" class="rname" data-act="volume-steps" data-focus="volume-steps">${options}</select>`
      + '<div class="meta">One press of volume up or volume down sends this many steps, one at a time. Use 1 unless you want bigger jumps.</div></div>';
  }

  // Admin setup view: rooms, connected inputs, and the Experimental sections. Room D keeps its own gate until its explanation is acknowledged.
  function renderSetup(m) {
    const rooms = m.rooms || [];
    const byId = Object.fromEntries(rooms.map(r => [r.id, r]));
    const acks = m.acks || {};
    const backLabel = m.backTo === 'rooms' ? 'Back to rooms' : 'Back to settings';   // where Back returns to
    let html = `<div class="sheet"><div class="shead"><button class="pill" data-act="back" data-focus="back">${backLabel}</button><span class="name">Rooms and inputs</span></div>`;
    html += `<p class="meta">${esc(m.mapping || MAPPING_FALLBACK)}</p>`;
    for (const r of rooms.filter(r => r.id !== 'd')) {
      html += roomRow(r, false);   // Rooms A to C have only verified commands, so they have no Experimental section
    }
    html += '<div class="dhead">Infrared remote</div>' + irLine(m.irTarget) + irSettings(m);
    html += volumeSettings(m);
    html += `<div class="dhead">Bridge computer</div><div class="rrow bridge-row"><label class="rlabel" for="bridge-name">Name of this bridge computer</label>`
      + `<input id="bridge-name" class="rname" data-act="bridge-name" data-focus="bridge-name" value="${esc(m.bridgeName || DEFAULT_BRIDGE_NAME)}" maxlength="40">`
      + '<div class="meta">Shown at the top of the card and in diagnostics. Enter 1 to 40 characters.</div>'
      + '<div class="brow"><button class="ctl" data-act="bridge-reset" data-focus="bridge-reset">Use the default name</button></div></div>';
    html += '<div class="dhead">Connected inputs</div><p class="meta">Only connected inputs appear in the daily controls.</p>';
    for (const src of m.sources || []) {
      const input = (m.inputs || {})[src.source] || { name: src.label, icon: DEFAULT_ICON[src.source] || 'input' };
      const id = esc(src.source);
      html += `<div class="src-block">`
        + `<label class="rrow"><span class="rlabel">${esc(src.label)}</span><span class="sw"><input type="checkbox" data-act="source" data-source="${id}" data-focus="source:${id}" aria-label="${esc(src.label)} connected"${src.connected ? ' checked' : ''}><span></span></span></label>`
        + `<div class="rrow nick"><label class="rlabel" for="input-name-${id}">Nickname</label>`
        + `<input id="input-name-${id}" class="rname" data-act="input-name" data-source="${id}" data-focus="input-name:${id}" value="${esc(input.name)}" maxlength="30" placeholder="${esc(src.label)}">`
        + `<div class="brow"><button class="ctl" data-act="input-default" data-source="${id}" data-focus="input-default:${id}">Use default</button></div></div>`
        + iconPicker(src.source, src.label, input.icon) + '</div>';
    }
    html += `<div class="dhead">Experimental</div><p class="meta" id="experimental-text">${esc(EXPERIMENTAL_TEXT)}</p>`;
    html += ackGate('d', !!acks.d || !!(byId.d && byId.d.enabled));   // on, Room D's gate stays ticked
    if (byId.d) html += roomRow(byId.d, !acks.d && !byId.d.enabled);   // locks turning it on only, never off
    if (m.confirm) {
      html += `<div class="confirm"><p>${esc(m.confirm.text)}</p><div class="row"><button class="ctl" data-act="confirm-no" data-focus="confirm-no">Cancel</button><button class="ctl warn" data-act="confirm-yes" data-focus="confirm-yes">Enable</button></div></div>`;
    }
    // The error text is announced by the one alert region (see BoseControlCard.announce); this line is the visible copy.
    if (m.error) html += `<p class="err">${esc(m.error)}</p><div class="row">${dismissControl()}</div>`;
    return html + '</div>';
  }

  function renderSettings(m) {
    if (m.view === 'setup') return renderSetup(m);
    const manage = typeof m.manage_url === 'string' && /^\/[^/\\]/.test(m.manage_url) ? m.manage_url : MANAGE_DEFAULT;
    let html = '<div class="sheet"><div class="shead"><span class="name">Settings</span><button class="pill" data-act="close" data-focus="close">Done</button></div>';
    html += '<div class="row"><button class="ctl" data-act="setup" data-focus="setup">Set up rooms and inputs</button></div>';
    if (m.error) html += `<p class="err">${esc(m.error)}</p>`;
    html += `<div class="diag"><div class="dhead">Diagnostics</div><div class="meta">Bridge computer: ${esc(m.bridgeName || DEFAULT_BRIDGE_NAME)}</div><div class="meta">Current fault: ${esc(m.fault || 'none')}</div>`;
    if (m.events === null) html += '<p class="err">Couldn\'t load diagnostics.</p>';
    else if (!m.events || !m.events.length) html += '<p class="meta">No recent events.</p>';
    html += '<details class="tech"><summary>Technical details</summary>';
    if (m.code) html += `<div class="meta">Last error code: ${esc(m.code)}</div>`;
    html += '<ol class="events">';
    for (const e of m.events || []) {
      html += `<li><time>${esc(new Date(e.t * 1000).toLocaleTimeString())}</time> <b>${esc(e.kind)}</b> ${esc(e.action || '')} <span>${esc(e.detail)}</span></li>`;
    }
    html += '</ol></details><div class="row"><button class="ctl" data-act="copy" data-focus="copy">Copy</button></div>'
      + `<div class="links"><a href="/config/logs?filter=bose_lifestyle">Open Home Assistant logs</a> <a href="${esc(manage)}">Remote setup</a></div></div></div>`;
    return html;
  }

  // The failure class of a press error: its exact message, else its translation key or code, else uncertain (the safe reading).
  function failureCode(error) {
    const text = error && error.message;
    const byText = Object.keys(MESSAGES).find(code => MESSAGES[code] === text);
    if (byText) return byText;
    const key = error && (error.translation_key || error.code);
    return key && MESSAGES[key] ? key : 'uncertain';
  }

  // Only the server's own name messages are shown as text; any other failure gets the generic line.
  function failedSave(error, ...codes) {
    return !!error && codes.includes(error.code) && typeof error.message === 'string';
  }

  // Setup saves: only the bridge's definite refusals say nothing changed; anything else may have been committed, so it is uncertain.
  function setupFailure(error) {
    const code = error && error.code;
    if (code === 'not_saved') return SETUP_MESSAGES.not_saved;
    return DEFINITE_REFUSALS.includes(code) ? SETUP_MESSAGES.generic : SETUP_MESSAGES.uncertain;
  }

  function eventsText(events) {
    return (events || []).map(e => `${new Date(e.t * 1000).toLocaleString()} ${e.kind} ${e.action || ''} ${e.detail}`.trim()).join('\n');
  }

  class Controller {
    constructor({ storage, haptic, now, onChange }) {
      this.storage = storage; this.haptic = haptic || (() => {}); this.now = now || Date.now; this.onChange = onChange || (() => {});
      this.hass = null; this.layout = null; this.loadError = false; this.started = false; this.lastTry = 0;
      this.reloading = false; this.layoutConfiguration = null;
      this.openId = null; this.openSet = false; this.notice = null; this.sheet = null;
      this.blockedUntil = {}; this.slowUntil = 0; this.lastCode = null;   // entity -> ms until a not-sent control is enabled again; last failure code (technical details only)
      this.busy = {}; this.inflight = {};
      this.lastSent = {};   // room id -> the source key of its most recent successful source press (Last sent marker)
      this.opener = 'gear';   // the data-focus key that opened the sheet; focus returns there on close   // settings requests in flight, by key: a second submit of the same change is ignored
    }

    read() { try { return this.storage.getItem(STORE_KEY); } catch (e) { return null; } }
    write(id) { try { this.storage.setItem(STORE_KEY, id); } catch (e) { /* private mode: remembering is a convenience */ } }

    setHass(hass) {
      this.hass = hass;
      if (!this.started) { this.started = true; this.reload(); return; }
      const configuration = this.layout ? JSON.stringify(((hass.states || {})[this.layout.health]?.attributes || {}).configuration || null) : null;
      // Bridge configuration changes refresh immediately; infrequent reads also pick up another client's nicknames.
      if ((this.layout && configuration !== this.layoutConfiguration) || this.now() - this.lastTry >= 30000) { this.reload(); return; }
      this.onChange('hass');
    }

    async reload() {
      if (this.reloading) return;
      this.reloading = true;
      this.lastTry = this.now();
      try { this.layout = await this.hass.callWS({ type: 'bose_lifestyle/layout' }); this.loadError = false; }
      catch (e) { this.layout = null; this.loadError = true; }
      this.layoutConfiguration = this.layout ? JSON.stringify(((this.hass.states || {})[this.layout.health]?.attributes || {}).configuration || null) : null;
      this.reloading = false;
      const ids = this.layout ? this.layout.rooms.filter(r => r.enabled !== false).map(r => r.id) : [];
      if (!this.openSet || (this.openId !== null && !ids.includes(this.openId))) {
        this.openId = pickOpenRoom(ids, this.read()); this.openSet = true;
      }
      this.onChange();
    }

    view() {
      const hass = this.hass || {};
      const admin = !!(hass.user && hass.user.is_admin);   // administrators keep the settings and diagnostics while the bridge is down
      if (!this.layout) return { rooms: [], admin, dim: false, message: this.loadError ? NOT_READY : null, notice: this.notice,
        sheet: this.sheet ? { ...this.sheet, manage_url: MANAGE_DEFAULT } : null, irTarget: null, setupNeeded: false, bridgeName: DEFAULT_BRIDGE_NAME };
      const states = hass.states || {};
      const health = states[this.layout.health];
      const healthAttrs = (health && health.attributes) || {};
      const ready = controlsReady(health && health.state);
      const now = this.now();
      const blocked = entity => (this.blockedUntil[entity] || 0) > now;
      const outcome = states[this.layout.outcome];
      const attrs = outcome ? outcome.attributes : null;
      const nowSec = this.now() / 1000;
      const inputs = this.layout.inputs || {};
      const reportedRooms = healthAttrs.rooms || {};
      const rooms = this.layout.rooms.filter(room => room.enabled !== false).map(room => {
        const off = room.enabled === false;   // a zone turned off in settings stays listed, flagged and disabled
        const reported = reportedRooms[room.id] || {};
        const sourceName = value => {
          if (typeof value !== 'string' || !Object.prototype.hasOwnProperty.call(DEFAULT_SOURCE_LABELS, value)) return null;
          const configured = inputs[value];
          return configured && typeof configured.name === 'string' && configured.name.trim()
            ? configured.name.trim() : DEFAULT_SOURCE_LABELS[value];
        };
        return {
          id: room.id, name: room.name, open: this.openId === room.id, disabled: !ready || off,
          caption: off ? 'Turned off in settings' : lastSent(attrs, room.id, nowSec, room.name, inputs),
          telemetry: {
            volume: telemetryLine(reported, 'volume', 'Volume', nowSec,
              value => Number.isSafeInteger(value) && value >= 0 && value <= 255 ? String(value) : null),
            source: telemetryLine(reported, 'source', 'Selected input', nowSec, sourceName),
          },
          buttons: Object.fromEntries(Object.entries(room.buttons).map(([key, entity]) => {
            const st = states[entity];
            const reason = (room.unavailable && room.unavailable[key]) || null;
            const cooling = isSlowKey(key) && this.slowBlocked();
            const spec = { entity, disabled: !st || st.state === 'unavailable' || blocked(entity) || cooling, reason, flag: blocked(entity) ? 'Not sent' : null };
            const input = sourceOf(key) ? inputs[sourceOf(key)] : null;
            if (input) { spec.label = input.name; spec.icon = input.icon; }
            return [key, spec];
          })),
          lastSent: this.lastSent[room.id] || null,
        };
      });
      const sheet = this.sheet ? { ...this.sheet, fault: healthAttrs.fault || null, config: healthAttrs.config || null, code: this.lastCode, manage_url: this.layout.manage_url,
        irTarget: this.layout.ir_target || null, irLabel: this.layout.ir_receiver_label || null,
        volumeSteps: this.layout.volume_steps || 1 } : null;
      const empty = rooms.length === 0;   // nothing to show is the same as not ready; an administrator still gets the settings icon
      const configUncertain = healthAttrs.config === 'config-uncertain' || this.layout.reason === 'config-uncertain';
      let message = UNAVAILABLE;
      if (healthAttrs.fault) message = MESSAGES.fault_paused;
      else if (configUncertain) message = MESSAGES.config_uncertain;   // checked before "not ready": an empty room list is not the same as not ready
      else if (empty) message = NOT_READY;
      else if (ready) message = null;
      return { rooms, admin, dim: !ready && !empty, message, faultMessage: healthAttrs.fault ? String(healthAttrs.fault_message || '').trim() : null,
        notice: this.notice, sheet, irTarget: this.layout.ir_target || null, setupNeeded: this.layout.needs_setup === true,
        bridgeName: this.layout.bridge_name || DEFAULT_BRIDGE_NAME };
    }

    toggleRoom(id) {
      if (this.openId === id) this.openId = null; else { this.openId = id; this.write(id); }
      this.onChange();
    }

    slowBlocked() { return (this.slowUntil || 0) > this.now(); }

    async tap(entity, room, key) {
      if ((this.blockedUntil[entity] || 0) > this.now()) return;   // not sent a moment ago: no queue, no repeat
      if (key && isSlowKey(key)) {
        if (this.slowBlocked()) return;                          // power and input changes wait for the previous one
        this.slowUntil = this.now() + SLOW_COOLDOWN_MS;          // set before sending: a dropped or uncertain press still counts
        if (typeof setTimeout === 'function') setTimeout(() => this.onChange(), SLOW_COOLDOWN_MS + 25);   // re-enable the tiles when the wait ends
      }
      // With more than one volume step per press, a press that is still on its way is not followed by another for the same
      // control: a multi-step press takes longer than the repeat interval, and a held key must not queue presses behind
      // it. With one step per press every tap is sent, as before.
      // Every volume call is counted, whatever the setting is when it starts, so a setting change while one is on its way
      // still finds it.
      const volume = !!key && VOLUME_KEYS.has(key);
      const guarded = volume && !!this.layout && this.layout.volume_steps > 1;
      if (guarded && (this.inflight[entity] || 0) > 0) return;
      if (volume) this.inflight[entity] = (this.inflight[entity] || 0) + 1;
      this.notice = null; this.haptic('light');
      try {
        await this.hass.callService('button', 'press', { entity_id: entity });
        if (room && sourceOf(key)) this.lastSent[room] = key;   // only a press the bridge accepted moves the marker
      }
      catch (e) {
        const code = failureCode(e);
        this.lastCode = code; this.notice = MESSAGES[code]; this.haptic('failure');
        if (code === 'not_sent' && VOLUME_KEYS.has(key)) {
          // The bridge drops a volume press that follows another within its safety interval, and nothing was
          // changed. Tapping quickly is normal for volume, so say nothing and keep the control usable.
          this.notice = null;
        } else if (code === 'not_sent') {
          this.blockedUntil[entity] = this.now() + NOT_SENT_MS;
          if (typeof setTimeout === 'function') setTimeout(() => this.onChange(), NOT_SENT_MS);   // re-enable the control when the wait ends
        }
      } finally {
        if (volume) {
          const left = (this.inflight[entity] || 1) - 1;
          if (left > 0) this.inflight[entity] = left; else delete this.inflight[entity];
        }
      }
      this.onChange();
    }

    async openSettings() {
      if (!(this.hass.user && this.hass.user.is_admin)) return;
      this.sheet = { view: 'rooms', rooms: null, events: [], confirm: null, error: null, sources: [], acks: {} };
      this.onChange();
      await this.refreshSheet();
    }

    async openSetup() {
      if (!(this.hass.user && this.hass.user.is_admin)) return;
      const fromSettings = !!this.sheet;   // opened from Settings, or from Finish setup on the rooms
      if (!this.sheet) this.sheet = { view: 'rooms', rooms: null, events: [], confirm: null, error: null, sources: [], acks: {} };
      this.sheet.view = 'setup';
      this.sheet.backTo = fromSettings ? 'settings' : 'rooms';
      this.onChange();
      await this.refreshSheet();
    }

    goBack() {
      if (!this.sheet) return;
      if (this.sheet.backTo === 'rooms') this.sheet = null; else this.sheet.view = 'rooms';
      this.onChange();
    }

    dismiss() { this.notice = null; if (this.sheet) this.sheet.error = null; this.onChange(); }

    // The IR target: choosing another room only shows the warning and Save; nothing is sent until Save.
    pickIrZone(zone) {
      const s = this.sheet;
      if (!s) return;
      const current = this.layout && this.layout.ir_target ? this.layout.ir_target.zone : null;
      s.irPending = !zone || zone === current ? null : zone;
      this.onChange();
    }

    cancelIrZone() { if (this.sheet) this.sheet.irPending = null; this.onChange(); }

    async saveIrZone() {
      const s = this.sheet;
      if (!s || !s.irPending) return;
      const zone = s.irPending;
      return this.once('ir-zone', async () => {
        try {
          await this.hass.callWS({ type: 'bose_lifestyle/set_ir_zone', zone });
          if (this.sheet === s) { s.irPending = null; s.error = null; }
          this.notice = null;
          await this.reload();   // the layout shows the target the bridge now reports
        } catch (e) {
          if (this.sheet !== s) return;
          s.irPending = null;
          s.error = setupFailure(e);
          await this.refreshSheet();
          await this.reload();   // an uncertain save may have committed: the target shown is the bridge's
        }
        this.onChange();
      });
    }

    async setVolumeSteps(value) {
      const s = this.sheet;
      if (!s) return;
      const steps = Number(value);
      if (!Number.isInteger(steps) || steps < 1 || steps > 5) return;
      return this.once('volume-steps', async () => {
        try {
          await this.hass.callWS({ type: 'bose_lifestyle/set_volume_steps', steps });
          if (this.sheet === s) s.error = null;
          this.notice = null;
          await this.reload();
        } catch (e) {
          if (this.sheet !== s) return;
          s.error = setupFailure(e);
          await this.reload();   // an uncertain save may have committed: the value shown is the bridge's
        }
        this.onChange();
      });
    }

    // The receiver's label: trimmed; a blank field clears it (sent as null). HA-side only, never sent to the bridge.
    async setIrLabel(label) {
      const s = this.sheet;
      if (!s) return;
      const text = String(label == null ? '' : label).trim() || null;
      return this.once('ir-label', async () => {
        try {
          await this.hass.callWS({ type: 'bose_lifestyle/set_ir_receiver_label', label: text });
          if (this.sheet === s) s.error = null;
          this.notice = null;
          await this.reload();
        } catch (e) {
          if (this.sheet !== s) return;
          s.error = failedSave(e, 'invalid_name') ? e.message : setupFailure(e);
          await this.refreshSheet();
        }
        this.onChange();
      });
    }

    // The Experimental explanation must be acknowledged before Room D can be switched on.
    ack(checked, room = 'd') { if (this.sheet) this.sheet.acks[room] = checked === true; this.onChange(); }

    closeSettings() { this.sheet = null; this.onChange(); }

    // A settings request already pending under the same key is not sent again.
    async once(key, fn) {
      if (this.busy[key]) return;
      this.busy[key] = true;
      try { await fn(); } finally { delete this.busy[key]; }
    }

    // Each request holds the sheet it was made for; if that sheet was closed or replaced while pending, its answer is dropped.
    async refreshSheet() {
      const s = this.sheet;
      if (!s) return;
      try {
        const settings = await this.hass.callWS({ type: 'bose_lifestyle/settings' });
        if (this.sheet !== s) return;
        s.rooms = settings.rooms; s.sources = settings.setup ? settings.setup.sources : [];
        s.mapping = settings.setup ? settings.setup.mapping : null;
        s.bridgeName = settings.bridge_name || DEFAULT_BRIDGE_NAME;
        s.inputs = settings.inputs || {};
      } catch (e) { if (this.sheet !== s) return; s.error = "Couldn't load settings."; }
      try {
        const events = (await this.hass.callWS({ type: 'bose_lifestyle/events', limit: 20 })).slice().reverse();
        if (this.sheet !== s) return;
        s.events = events;
      } catch (e) { if (this.sheet !== s) return; s.events = null; }   // null: could not load, not "no events"
      this.onChange();
    }

    async setEnabled(room, enabled) {
      const s = this.sheet;
      if (!s) return;
      if (room === 'd' && enabled && !s.acks.d) { s.error = 'Read and confirm the Experimental section first.'; this.onChange(); return; }
      if (room === 'd' && enabled) { s.confirm = { room, text: CONFIRM_D }; this.onChange(); return; }
      await this.apply({ room, enabled });
    }

    async confirm(accept) {
      const s = this.sheet;
      if (!s) return;
      const pending = s.confirm; s.confirm = null;
      if (accept && pending) await this.apply({ room: pending.room, enabled: true }); else this.onChange();
    }

    async setName(room, name) {
      const s = this.sheet;
      if (!s) return;
      return this.once(`name:${room}`, async () => {
        try {
          const result = await this.hass.callWS({ type: 'bose_lifestyle/set_room_name', room, name: String(name).trim() });
          if (this.sheet === s) {
            s.rooms = result.rooms; s.sources = result.setup.sources; s.error = null; this.notice = null;
          }
          await this.reload();   // the layout is refreshed even when the sheet was dismissed
        } catch (e) {
          if (this.sheet !== s) return;
          s.error = failedSave(e, 'duplicate_name', 'invalid_name') ? e.message : setupFailure(e);
          await this.refreshSheet();
        }
        this.onChange();
      });
    }

    // The nickname follows the room-name rules; a blank name is refused by the server, and reset sends null to clear it.
    async setBridgeName(name) { return this.saveBridgeName(String(name).trim()); }
    async resetBridgeName() { return this.saveBridgeName(null); }

    async saveBridgeName(name) {
      const s = this.sheet;
      if (!s) return;
      return this.once('bridge', async () => {
        try {
          const result = await this.hass.callWS({ type: 'bose_lifestyle/set_bridge_name', name });
          if (this.sheet === s) {
            s.rooms = result.rooms; s.sources = result.setup.sources; s.bridgeName = result.bridge_name; s.error = null; this.notice = null;
          }
          await this.reload();   // the layout is refreshed even when the sheet was dismissed
        } catch (e) {
          if (this.sheet !== s) return;
          s.error = failedSave(e, 'invalid_name') ? e.message : setupFailure(e);
          await this.refreshSheet();
        }
        this.onChange();
      });
    }

    async setExperimental(room, allow) {
      const s = this.sheet;
      if (!s) return;
      if (allow && !s.acks[room]) { s.error = 'Read and confirm the Experimental section first.'; this.onChange(); return; }
      return this.once(`exp:${room}`, async () => {
        try {
          const result = await this.hass.callWS({ type: 'bose_lifestyle/set_experimental', room, allow });
          if (this.sheet === s) {
            s.rooms = result.rooms; s.sources = result.setup.sources; s.error = null; this.notice = null;
          }
          await this.reload();   // the layout is refreshed even when the sheet was dismissed
        } catch (e) {
          if (this.sheet !== s) return;
          s.error = setupFailure(e);
          await this.refreshSheet();   // the switch shows what the server says, not what was asked
        }
        this.onChange();
      });
    }

    // A nickname or icon change. Only the field that changed is sent: an omitted field keeps its stored value; null is the default.
    async setInput(source, change) {
      const s = this.sheet;
      if (!s) return;
      return this.once(`input:${source}`, async () => {
        try {
          const result = await this.hass.callWS({ type: 'bose_lifestyle/set_input', source, ...change });
          if (this.sheet === s) {
            s.inputs = result.inputs || {}; s.sources = result.setup.sources; s.error = null; this.notice = null;
          }
          await this.reload();   // the layout is refreshed even when the sheet was dismissed
        } catch (e) {
          if (this.sheet !== s) return;
          s.error = failedSave(e, 'invalid_name', 'invalid_icon') ? e.message : setupFailure(e);
          await this.refreshSheet();
        }
        this.onChange();
      });
    }

    async setSource(source, connected) {
      const s = this.sheet;
      if (!s) return;
      return this.once(`src:${source}`, async () => {
        try {
          const result = await this.hass.callWS({ type: 'bose_lifestyle/set_source', source, connected });
          if (this.sheet === s) {
            s.sources = result.setup.sources; s.error = null; this.notice = null;
          }
          await this.reload();   // the layout is refreshed even when the sheet was dismissed
        } catch (e) {
          if (this.sheet !== s) return;
          s.error = setupFailure(e);
          await this.refreshSheet();
        }
        this.onChange();
      });
    }

    async apply(change) {
      const s = this.sheet;
      if (!s) return;
      return this.once(`set:${change.room}`, async () => {
        try {
          const rooms = (await this.hass.callWS({ type: 'bose_lifestyle/set_room', ...change })).rooms;
          if (this.sheet === s) {
            s.rooms = rooms; s.error = null; this.notice = null;
          }
          this.notice = null;
          await this.reload();   // the layout is refreshed even when the sheet was dismissed
        } catch (e) {
          this.lastCode = (e && e.code) || 'unknown';
          if (this.sheet !== s) return;
          s.error = e && e.code === 'partial_failure' && typeof e.message === 'string' ? e.message
            : setupFailure(e);
          await this.refreshSheet();   // the switch shows what the server says, not what was asked
          await this.reload();
        }
        this.onChange();
      });
    }

    eventsText() { return eventsText(this.sheet && this.sheet.events); }
  }

  globalThis.BoseCard = { renderMain, renderSetup, pickOpenRoom, lastSent, controlsReady, renderRoom, actionLabel, relativeTime, esc, icon, ICONS, KEY_LABELS,
    Controller, renderSettings, generateView, generateDashboard, CONFIRM_D, eventsText, shouldRender, irLine, REASON_TEXT, MESSAGES, INPUT_ICONS, sourceOf };
  if (typeof customElements === 'undefined') return;   // loaded by node for the logic tests

  const STYLE = `
    /* Arc-style roles built from the Home Assistant theme, so the card follows any HA theme and dark mode and its rooms
       match the cards around it, both on its own dashboard and inside an existing one. Sizes sit on a 4 px grid. */
    :host {
      --bc-surface: var(--ha-card-background, var(--card-background-color, #fff));
      --bc-fg: var(--primary-text-color, #212121);
      --bc-fg-2: var(--secondary-text-color, #727272);
      --bc-accent: var(--primary-color, #03a9f4);
      --bc-on-accent: var(--text-primary-color, #fff);
      --bc-danger: var(--error-color, #db4437);
      --bc-border: var(--ha-card-border-color, var(--divider-color, rgba(0, 0, 0, .12)));
      --bc-border-subtle: color-mix(in srgb, var(--bc-fg) 10%, transparent);
      --bc-border-strong: color-mix(in srgb, var(--bc-fg) 24%, transparent);
      --bc-fill: color-mix(in srgb, var(--bc-fg) 6%, transparent);
      --bc-fill-strong: color-mix(in srgb, var(--bc-fg) 12%, transparent);
      --bc-pad: 16px;
      --bc-radius-surface: var(--ha-card-border-radius, var(--ha-border-radius-lg, 12px));
      --bc-radius-control: max(8px, calc(var(--bc-radius-surface) - var(--bc-pad)));   /* concentric with the room surface */
      --bc-ease: cubic-bezier(.2, 0, 0, 1);
      display: block; position: relative; max-width: 480px; margin: 0 auto; font-size: 16px; line-height: 1.4; letter-spacing: -.01em;
      color: var(--bc-fg); -webkit-text-size-adjust: 100%; text-size-adjust: 100%; padding: 0 env(safe-area-inset-right) 0 env(safe-area-inset-left);
    }
    * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
    button { font: inherit; letter-spacing: inherit; color: inherit; border: 0; background: none; cursor: pointer; touch-action: manipulation; }
    .app { display: flex; flex-direction: column; gap: 8px; transition: opacity .2s var(--bc-ease); }
    .app.dim { opacity: .55; }
    .room, .sheet { background: var(--bc-surface); color: var(--bc-fg); border: var(--ha-card-border-width, 1px) solid var(--bc-border); border-radius: var(--bc-radius-surface); box-shadow: var(--ha-card-box-shadow, none); padding: 12px var(--bc-pad) var(--bc-pad); }
    .head { display: flex; align-items: center; gap: 12px; min-height: 44px; }
    .title { flex: 1; min-width: 0; text-align: left; padding: 0; min-height: 44px; }
    .name { display: block; min-width: 0; overflow-wrap: anywhere; font-size: 1.125em; font-weight: 500; line-height: 1.3; }
    .cap { display: block; font-size: .875em; color: var(--bc-fg-2); margin-top: 2px; }
    .why { font-size: .875em; color: var(--bc-fg-2); padding-top: 4px; }
    .telemetry { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 4px 16px; margin: 8px 0 0; font-size: .875em; overflow-wrap: anywhere; }
    .telemetry dt { color: var(--bc-fg-2); }
    .telemetry dd { margin: 0; font-variant-numeric: tabular-nums; }
    .chev { color: var(--bc-fg-2); display: flex; transition: transform .2s var(--bc-ease); }
    .open .chev { transform: rotate(180deg); }
    .ctl { display: flex; align-items: center; justify-content: center; gap: 8px; min-height: 44px; min-width: 44px; padding: 0 16px; border: 1px solid var(--bc-border-strong); border-radius: var(--bc-radius-control); font-size: .875em; font-weight: 500; transition: transform .1s var(--bc-ease), opacity .15s, background-color .15s; }
    .ctl:active { transform: scale(.97); background: var(--bc-fill); }
    .ctl:disabled { opacity: .4; cursor: default; }
    .ctl.warn, .pill { background: var(--bc-accent); border-color: var(--bc-accent); color: var(--bc-on-accent); }
    /* Power: two commands side by side. Neither is shown as selected: the bridge does not report power state. */
    .seg { display: flex; flex-wrap: wrap; gap: 2px; padding: 2px; border-radius: calc(var(--bc-radius-control) + 2px); background: var(--bc-fill-strong); }
    .seg .ctl { flex: 1; border: 0; border-radius: var(--bc-radius-control); min-height: 44px; }
    .seg .ctl:active { background: var(--bc-surface); }
    .power .ctl svg { display: none; }
    /* Source tiles: 2 columns at 320 to 439 px, 3 from 440 px (480 px cap). "Last sent" marks the last press, not the playing input. */
    .seg.src { display: grid; grid-template-columns: repeat(auto-fit, minmax(8em, 1fr)); gap: 8px; margin-top: 16px; background: none; padding: 0; border-radius: 0; }
    .seg.src .ctl { flex-direction: column; gap: 4px; min-height: 56px; padding: 8px; border: 1px solid var(--bc-border-strong); border-radius: var(--bc-radius-control); background: var(--bc-fill); text-align: center; line-height: 1.3; font-weight: 400; }
    .seg.src .ctl svg { flex: 0 0 auto; }
    .seg.src .ctl span { min-width: 0; overflow-wrap: anywhere; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; text-overflow: ellipsis; }
    .tag { display: inline-block; font-size: .857em; font-style: normal; font-weight: 500; color: var(--bc-accent); }
    .vol { display: flex; margin-top: 16px; border: 1px solid var(--bc-border-strong); border-radius: var(--bc-radius-control); overflow: hidden; }
    .vol .ctl { flex: 1; min-height: 50px; border: 0; border-radius: 0; }
    .vol .ctl span, .icon-only span { display: none; }
    .vol .ctl + .ctl { border-left: 1px solid var(--bc-border-strong); }
    .row { display: flex; gap: 8px; margin-top: 8px; }
    .row .ctl { flex: 1; }
    /* Header: the bridge name as a section title, with the settings icon on the same line. */
    .bhead { padding: 8px 52px 4px 4px; }
    .bhead .name { font-size: 1.375em; font-weight: 500; line-height: 1.2; }
    .gear { position: absolute; top: 0; right: env(safe-area-inset-right); color: var(--bc-fg-2); min-width: 44px; min-height: 44px; display: flex; align-items: center; justify-content: center; border-radius: var(--bc-radius-control); }
    .setup { display: flex; justify-content: center; }
    .ir { text-align: center; font-size: .875em; color: var(--bc-fg-2); padding: 4px 8px; }
    .ir.warn, .err { color: var(--bc-danger); }
    .msg, .notice { text-align: center; font-size: .875em; color: var(--bc-fg-2); padding: 4px 8px; }
    .disclaimer { margin: 0; padding: 4px 8px 0; text-align: center; }
    .notice .ctl { margin: 8px auto 0; }
    /* Settings sheet. */
    .shead { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 8px; }
    .pill { border-radius: var(--bc-radius-control); padding: 0 16px; min-height: 44px; font-size: .875em; font-weight: 500; }
    .rrow { display: grid; grid-template-columns: 1fr auto; gap: 4px 12px; align-items: center; padding: 12px 0; border-top: 1px solid var(--bc-border-subtle); }
    .rrow > * { min-width: 0; }
    .rrow input[type=checkbox] { width: 22px; height: 22px; margin: 0; flex: 0 0 auto; accent-color: var(--bc-accent); }   /* the row is the 44 px target */
    .rrow.ack { display: flex; align-items: center; justify-content: flex-start; gap: 12px; min-height: 44px; }
    .ir-row > * { grid-column: 1 / -1; }   /* the label, the field and the selector each take a full row */
    .rname { font: inherit; letter-spacing: inherit; font-size: 1em; color: inherit; background: transparent; border: 1px solid var(--bc-border-strong); border-radius: var(--bc-radius-control); min-height: 44px; width: 100%; padding: 0 12px; }
    .rname:focus { border-color: var(--bc-accent); }
    .room-row .field-label, .room-row .rname { grid-column: 1 / -1; }
    .room-row .field-label { min-height: 0; padding-top: 4px; }
    .helper { margin: 4px 0 0; }
    .exp-row.locked, .room-row.locked .rlabel, .room-row.locked .rname { opacity: .45; }
    .rlabel { font-size: .875em; font-weight: 500; min-height: 44px; display: flex; align-items: center; }
    .meta { grid-column: 1 / -1; font-size: .875em; color: var(--bc-fg-2); }
    .bridge-row { grid-template-columns: 1fr; }   /* the name field and its reset button always stack, from 320 px up */
    .brow { display: flex; }
    .src-block { border-top: 1px solid var(--bc-border-subtle); padding-top: 4px; }
    .nick { grid-template-columns: 1fr; }
    fieldset.icons { border: 0; margin: 0 0 8px; padding: 0; min-width: 0; }
    .icons legend { font-size: .875em; color: var(--bc-fg-2); padding: 0; margin-bottom: 4px; }
    .ico-grid { display: flex; flex-wrap: wrap; gap: 8px; }
    .ico { position: relative; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; min-height: 44px; min-width: 5em; font-size: .875em; }
    .ico input { position: absolute; inset: 0; width: 100%; height: 100%; margin: 0; opacity: 0; z-index: 1; }
    .ico-box { display: flex; align-items: center; justify-content: center; width: 44px; height: 44px; border-radius: var(--bc-radius-control); border: 1px solid var(--bc-border-strong); }
    .ico input:checked ~ .ico-box { border: 2px solid var(--bc-accent); background: color-mix(in srgb, var(--bc-accent) 14%, transparent); color: var(--bc-accent); }
    .ico input:checked ~ .ico-txt { font-weight: 500; }
    /* The Experimental gate: a bordered box with a drawn checkbox (22 px, 2 px border, accent fill and a check when ticked). */
    .gate { display: flex; align-items: center; gap: 12px; min-height: 44px; margin: 12px 0; padding: 12px; border-radius: var(--bc-radius-control); border: 1px solid var(--bc-border-strong); cursor: pointer; font-size: .875em; }
    .gate-text { flex: 1; min-width: 0; }
    .gate-state { font-weight: 500; color: var(--bc-accent); }
    .cbox { -webkit-appearance: none; appearance: none; flex: 0 0 auto; width: 22px; height: 22px; margin: 0; border: 2px solid var(--bc-fg-2); border-radius: 6px; background-color: transparent; background-position: center; background-repeat: no-repeat; background-size: 16px 16px; }
    .cbox:checked { background-color: var(--bc-accent); border-color: var(--bc-accent); background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath d='M3.5 8.5l3 3 6-7' fill='none' stroke='white' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E"); }
    .sw { position: relative; width: 51px; height: 44px; }   /* a 44 px hit area around the 31 px track */
    .sw input { position: absolute; inset: 0; opacity: 0; margin: 0; width: 100%; height: 100%; z-index: 1; }
    .sw span { position: absolute; left: 0; right: 0; top: 6.5px; bottom: 6.5px; border-radius: 999px; background: var(--bc-fill-strong); transition: background .2s var(--bc-ease); }
    .sw span::after { content: ""; position: absolute; top: 2px; left: 2px; width: 27px; height: 27px; border-radius: 50%; background: var(--bc-on-accent); box-shadow: 0 1px 2px rgba(0, 0, 0, .2); transition: transform .2s var(--bc-ease); }
    .sw input:checked + span { background: var(--bc-accent); }
    .sw input:checked + span::after { transform: translateX(20px); }
    .confirm { margin: 8px 0; padding: 12px; border-radius: var(--bc-radius-control); border: 1px solid var(--bc-border-strong); font-size: .875em; }
    .confirm p { margin: 0 0 8px; }
    .err { font-size: .875em; }
    .diag { margin-top: 12px; border-top: 1px solid var(--bc-border-subtle); padding-top: 12px; }
    .dhead { font-weight: 500; margin-bottom: 4px; }
    .events { max-height: 200px; overflow: auto; padding-left: 20px; font-size: .75em; margin: 8px 0; }
    .events span { color: var(--bc-fg-2); word-break: break-all; }
    .links { display: flex; gap: 16px; margin-top: 12px; font-size: .875em; }
    .links a { display: inline-flex; align-items: center; min-height: 44px; color: var(--bc-accent); text-decoration: none; }
    summary { display: flex; align-items: center; min-height: 44px; cursor: pointer; }
    .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
    /* Keyboard focus only (never on pointer focus); the ring is the card's text color, so it holds on light and dark themes. */
    button:focus-visible, input:focus-visible, select:focus-visible, a:focus-visible, summary:focus-visible { outline: 3px solid var(--bc-fg); outline-offset: 2px; }
    .sw input:focus-visible + span { outline: 3px solid var(--bc-fg); outline-offset: 2px; }
    .ico input:focus-visible ~ .ico-box { outline: 3px solid var(--bc-fg); outline-offset: 2px; }
    .cbox:focus-visible { outline: 3px solid var(--bc-fg); outline-offset: 2px; }
    /* Not-sent and other state changes are instant when the user asks for reduced motion. */
    @media (prefers-reduced-motion: reduce) {
      .app, .chev, .ctl, .sw span, .sw span::after { transition: none; }
      .ctl:active { transform: none; }
    }
  `;

  class BoseControlCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
      // Two fixed live regions outside the re-rendered root: one polite status, one assertive error (role alert).
      this.shadowRoot.innerHTML = `<style>${STYLE}</style><div id="status" class="sr" role="status" aria-live="polite"></div>`
        + '<div id="alert" class="sr" role="alert" aria-live="assertive"></div><div id="root"></div>';
      this.rootEl = this.shadowRoot.getElementById('root');
      this.statusEl = this.shadowRoot.getElementById('status');
      this.alertEl = this.shadowRoot.getElementById('alert');
      this.last = '';
      this.volumeHoldActive = false; this.volumeCalls = new Set(); this.volumeRefreshTimer = null;
      this.focusNext = null;   // data-focus key to focus after the next render (opener of the sheet, or the control that replaced the focused one)
      let storage = null;
      try { storage = window.localStorage; } catch (e) { storage = null; }
      this.controller = new Controller({
        storage: storage || { getItem: () => null, setItem: () => {} },
        haptic: kind => { try { window.dispatchEvent(new CustomEvent('haptic', { detail: kind })); } catch (e) { /* no haptics */ } },
        now: () => Date.now(),
        onChange: reason => this.render(reason),
      });
      this.shadowRoot.addEventListener('click', e => this.onClick(e));
      this.shadowRoot.addEventListener('pointerdown', e => this.onPointerDown(e));
      for (const type of ['pointerup', 'pointercancel', 'pointerleave']) this.shadowRoot.addEventListener(type, e => this.endPointer(e));
      this.shadowRoot.addEventListener('change', e => this.onChange(e));
      this.shadowRoot.addEventListener('keydown', e => this.onKeydown(e));
      this.endHold = () => this.stopHold();                              // blur, hidden page, removal: always ends a hold
      this.endPointer = e => {                                           // a release of a different pointer does not end this hold
        if (e && e.pointerId !== undefined && this.holdPointerId !== undefined && e.pointerId !== this.holdPointerId) return;
        if (e && e.type === 'pointerleave' && e.buttons) {
          clearTimeout(this.holdTimer); clearInterval(this.holdRepeat);
          this.holdTimer = null; this.holdRepeat = null;
          return;                                                         // do not refresh until the still-held pointer is released
        }
        this.stopHold();
      };
    }

    // A hold ends when the pointer is released anywhere, the window loses focus or is hidden, or the card is removed.
    connectedCallback() {
      for (const type of ['pointerup', 'pointercancel']) window.addEventListener(type, this.endPointer);
      window.addEventListener('blur', this.endHold);
      document.addEventListener('visibilitychange', this.endHold);
    }

    disconnectedCallback() {
      for (const type of ['pointerup', 'pointercancel']) window.removeEventListener(type, this.endPointer);
      window.removeEventListener('blur', this.endHold);
      document.removeEventListener('visibilitychange', this.endHold);
      this.stopHold();
    }

    setConfig() { /* zero configuration */ }
    // Masonry views balance columns by this (1 unit is about 50 px): the header plus one closed room per row.
    getCardSize() {
      const layout = this.controller && this.controller.layout;
      return 1 + 2 * (layout && Array.isArray(layout.rooms) ? layout.rooms.length : 2);
    }
    // Sections views: full section width by default, never narrower than half; the height follows the content.
    getGridOptions() { return { columns: 12, min_columns: 6, rows: 'auto' }; }
    static getStubConfig() { return {}; }
    set hass(hass) { this.controller.setHass(hass); }

    render(reason) {
      const c = this.controller, v = c.view();
      const html = v.sheet ? renderSettings(v.sheet) : renderMain(v);
      const focused = this.shadowRoot.activeElement;
      const typing = !!(focused && focused.classList && focused.classList.contains('rname'));
      // Focus goes back to the same control by its stable data-focus key after the rebuild.
      const want = this.focusNext || (focused && focused.dataset && focused.dataset.focus) || null;
      this.focusNext = null;
      this.announce(v, reason !== 'hass');   // an own change (a press or a save) is announced even when it repeats
      if (!shouldRender({ sheet: !!v.sheet, reason, html, last: this.last, typing })) return;
      this.last = html;
      this.rootEl.innerHTML = html;
      if (want) {
        const el = this.rootEl.querySelector(`[data-focus="${want}"]`);
        if (el) el.focus();
      }
    }

    // Errors (press refusals, uncertain or fault states, sheet errors) go to the alert region; other status to the polite one.
    announce(v, force) {
      const urgent = v.sheet ? (v.sheet.error || null)
        : (v.notice || (v.message === MESSAGES.fault_paused || v.message === MESSAGES.config_uncertain ? v.message : null));
      const polite = v.sheet ? null : (v.message && v.message !== urgent ? v.message : null);
      this.setRegion(this.alertEl, urgent, force);
      this.setRegion(this.statusEl, polite, force);
    }

    setRegion(el, text, force) {
      const next = text || '';
      if (el.textContent === next && !(force && next)) return;
      el.textContent = force && el.textContent === next ? '' : next;
      if (el.textContent !== next) el.textContent = next;
    }

    onKeydown(event) {
      const c = this.controller;
      if (!c.sheet) return;
      if (event.key === 'Escape') { this.focusNext = this.opener; c.closeSettings(); return; }
      if (event.key !== 'Tab') return;
      // Focus stays inside the open sheet: Tab past the last control wraps to the first, and back again.
      const items = [...this.rootEl.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), a[href], summary')];
      if (!items.length) return;
      const active = this.shadowRoot.activeElement;
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && (active === first || !items.includes(active))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (active === last || !items.includes(active))) { event.preventDefault(); first.focus(); }
    }

    onPointerDown(event) {
      // Volume: the first press happens now, and holding keeps repeating it until the pointer is released.
      const t = event.target.closest('[data-key]');
      if (!t || t.disabled || event.button > 0 || !REPEAT_KEYS.has(t.dataset.key)) return;
      const c = this.controller;
      this.stopHold();
      this.cancelVolumeRefresh();
      this.volumeHoldActive = true;
      this.volumePointerAt = c.now();
      this.holdPointerId = event.pointerId;
      this.trackVolumeCall(t.dataset.entity, t.dataset.room, t.dataset.key);
      this.holdTimer = setTimeout(() => {
        this.holdRepeat = setInterval(() => {
          if (!this.isConnected) return this.stopHold();   // the card was removed while held: send nothing more
          this.trackVolumeCall(t.dataset.entity, t.dataset.room, t.dataset.key);
        }, HOLD_REPEAT_MS);
      }, HOLD_START_MS);
    }

    trackVolumeCall(entity, room, key) {
      this.cancelVolumeRefresh();
      const call = Promise.resolve(this.controller.tap(entity, room, key));
      this.volumeCalls.add(call);
      const settled = () => { this.volumeCalls.delete(call); this.scheduleVolumeRefresh(); };
      call.then(settled, settled);
      return call;
    }

    cancelVolumeRefresh() {
      clearTimeout(this.volumeRefreshTimer);
      this.volumeRefreshTimer = null;
    }

    scheduleVolumeRefresh() {
      if (this.volumeHoldActive || this.volumeCalls.size) return;
      this.cancelVolumeRefresh();
      this.volumeRefreshTimer = setTimeout(() => {
        this.volumeRefreshTimer = null;
        if (this.volumeHoldActive || this.volumeCalls.size || !this.isConnected) return;
        const health = this.controller.layout && this.controller.layout.health;
        const hass = this.controller.hass;
        if (!health || !hass || typeof hass.callService !== 'function') return;
        Promise.resolve(hass.callService('homeassistant', 'update_entity', { entity_id: health })).catch(() => {});
      }, 450);
    }

    stopHold() {
      // The click that follows a release is judged from the release, not the press, so a long hold never adds a step.
      if (this.volumePointerAt !== undefined && this.controller) this.volumePointerAt = this.controller.now();
      const wasHolding = this.volumeHoldActive;
      this.volumeHoldActive = false;
      clearTimeout(this.holdTimer); clearInterval(this.holdRepeat);
      this.holdTimer = null; this.holdRepeat = null; this.holdPointerId = undefined;
      if (wasHolding) this.scheduleVolumeRefresh();
    }

    onClick(event) {
      const t = event.target.closest('[data-act],[data-key]');
      if (!t || t.disabled) return;
      const c = this.controller;
      // The pointer already sent this volume press; the click that follows the same touch must not send it again.
      if (t.dataset.key && REPEAT_KEYS.has(t.dataset.key) && this.volumePointerAt !== undefined && c.now() - this.volumePointerAt < 1000) {
        this.volumePointerAt = undefined; return;
      }
      if (t.dataset.key && VOLUME_KEYS.has(t.dataset.key)) return this.trackVolumeCall(t.dataset.entity, t.dataset.room, t.dataset.key);
      if (t.dataset.key) return c.tap(t.dataset.entity, t.dataset.room, t.dataset.key);
      switch (t.dataset.act) {
        case 'toggle': return c.toggleRoom(t.dataset.room);
        case 'gear': this.opener = 'gear'; this.focusNext = 'close'; return c.openSettings();
        case 'setup': if (!c.sheet) this.opener = 'setup'; this.focusNext = 'back'; return c.openSetup();
        case 'back': this.focusNext = 'setup'; return c.goBack();
        case 'bridge-reset': return c.resetBridgeName();
        case 'dismiss': this.focusNext = c.sheet ? 'back' : 'gear'; return c.dismiss();   // the control that was clicked is gone after this
        case 'ir-cancel': this.focusNext = 'ir-target'; return c.cancelIrZone();
        case 'ir-save': this.focusNext = 'ir-target'; return c.saveIrZone();
        case 'ir-label-save': return c.setIrLabel(this.rootEl.querySelector('#ir-label').value);
        case 'input-default': return c.setInput(t.dataset.source, { name: null });
        case 'close': this.focusNext = this.opener; return c.closeSettings();
        case 'confirm-yes': return c.confirm(true);
        case 'confirm-no': return c.confirm(false);
        case 'copy': {
          try { Promise.resolve(navigator.clipboard.writeText(c.eventsText())).catch(() => {}); } catch (e) { /* clipboard unavailable */ }
          return;
        }
        default: return;
      }
    }

    onChange(event) {
      const t = event.target, c = this.controller;
      if (t.dataset.act === 'name') c.setName(t.dataset.room, t.value);
      else if (t.dataset.act === 'bridge-name') c.setBridgeName(t.value);
      else if (t.dataset.act === 'enable') c.setEnabled(t.dataset.room, t.checked);
      else if (t.dataset.act === 'source') c.setSource(t.dataset.source, t.checked);
      else if (t.dataset.act === 'input-name') c.setInput(t.dataset.source, { name: t.value });
      else if (t.dataset.act === 'input-icon') c.setInput(t.dataset.source, { icon: t.dataset.icon });
      else if (t.dataset.act === 'ir-target') c.pickIrZone(t.value);
      else if (t.dataset.act === 'volume-steps') c.setVolumeSteps(t.value);
      else if (t.dataset.act === 'ack') c.ack(t.checked, t.dataset.room || 'd');
      else if (t.dataset.act === 'experimental') c.setExperimental(t.dataset.room, t.checked);
    }
  }

  class BoseStrategyView extends HTMLElement {
    static async generate() { return generateView(); }
  }

  class BoseStrategyDashboard extends HTMLElement {
    static async generate() { return generateDashboard(); }
  }

  function defineElement(tag, element) {
    const registry = window.customElements || globalThis.customElements;
    if (!registry) return;
    if (!registry.get(tag)) registry.define(tag, element);
    if (typeof registry.whenDefined !== 'function') return;
    void registry.whenDefined('home-assistant').then(() => {
      const current = window.customElements;
      if (current !== registry && !current.get(tag)) current.define(tag, element);
    });
  }
  defineElement('bose-control-card', BoseControlCard);
  defineElement('ll-strategy-view-bose-control', BoseStrategyView);
  defineElement('ll-strategy-dashboard-bose-control', BoseStrategyDashboard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: 'bose-control-card', name: 'Bose Lifestyle Assistant', description: 'Room controls for Bose Lifestyle systems. Unofficial, not affiliated with Bose.' });
})();
