// Card logic and rendering, run with plain node: node tests/test_bose_card.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
require('../custom_components/bose_lifestyle/frontend/bose-control-card.js');
const { pickOpenRoom, lastSent, controlsReady, renderRoom, actionLabel, relativeTime, Controller, renderSettings, generateView, generateDashboard, CONFIRM_D, shouldRender, eventsText, MESSAGES } = globalThis.BoseCard;
const FORBIDDEN = ['restart', 'reboot', 'unplug', 'power cycle', 'try again'];

(async () => {
  const make = () => new Controller({ storage: { getItem: () => null, setItem() {} }, now: () => 100000 });
  const settings = { rooms: [], setup: { sources: [] } };
  const c = make();
  c.sheet = { rooms: [], acks: {} };
  c.hass = { user: { is_admin: true }, callWS: async msg => {
    if (msg.type === 'bose_lifestyle/set_room') throw { code: 'uncertain' };
    if (msg.type === 'bose_lifestyle/layout') return { rooms: [] };
    return msg.type === 'bose_lifestyle/settings' ? settings : [];
  }};
  await c.apply({ room: 'a', enabled: true });
  assert.ok(c.sheet.error.includes("couldn't confirm") && !c.sheet.error.includes('Nothing was changed'));
  const lost = make();
  lost.loadError = true;
  lost.hass = { user: { is_admin: true }, callWS: async msg => msg.type === 'bose_lifestyle/settings' ? settings : [] };
  await lost.openSettings();
  assert.ok(lost.view().sheet, 'Diagnostics must open when layout is missing');
  const target = make();
  let zone = 'a', calls = 0;
  const hass = { user: { is_admin: false }, states: { 'sensor.health': { state: 'serial-open', attributes: { configuration: { ir: { zone } } } } }, callWS: async () => {
    calls++;
    return { health: 'sensor.health', rooms: [], ir_target: { zone, zone_name: zone, allowed: true } };
  }};
  target.hass = hass;
  await target.reload();
  target.started = true;
  zone = 'b';
  hass.states['sensor.health'].attributes.configuration = { ir: { zone } };
  target.setHass(hass); target.setHass(hass);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(target.view().irTarget.zone, 'b', 'Other-client target changes must refresh layout');
  assert.equal(calls, 2, 'Concurrent layout reads must coalesce');
  console.log('Uncertain room saves, missing-layout diagnostics and cross-client IR target refresh passed');
})().catch(error => { console.error(error); process.exitCode = 1; });

// A Home Assistant frontend load can replace the element registry after this add_extra_js_url module runs.
async function testRegistrySwap() {
  class Registry {
    elements = new Map();
    waiters = new Map();
    define(tag, element) {
      if (this.elements.has(tag)) throw new Error(`${tag} already defined`);
      this.elements.set(tag, element);
      for (const resolve of this.waiters.get(tag) || []) resolve(element);
    }
    get(tag) { return this.elements.get(tag); }
    whenDefined(tag) {
      const element = this.get(tag);
      return element ? Promise.resolve(element) : new Promise(resolve => {
        this.waiters.set(tag, [...(this.waiters.get(tag) || []), resolve]);
      });
    }
  }
  const native = new Registry();
  const window = { customElements: native, customCards: [] };
  const context = { window, customElements: native, HTMLElement: class {}, setTimeout };
  context.globalThis = context;
  vm.runInNewContext(fs.readFileSync(require.resolve('../custom_components/bose_lifestyle/frontend/bose-control-card.js'), 'utf8'), context);
  const card = native.get('bose-control-card');
  const viewStrategy = native.get('ll-strategy-view-bose-control');
  const dashboardStrategy = native.get('ll-strategy-dashboard-bose-control');
  for (const [name, element] of Object.entries({ card, viewStrategy, dashboardStrategy })) {
    assert.equal(typeof element, 'function', `${name} must be registered before the registry swap`);
  }
  const replacement = new Registry();
  window.customElements = replacement;
  context.globalThis.customElements = replacement;
  const app = class extends context.HTMLElement {};
  native.define('home-assistant', app);
  replacement.define('home-assistant', app);
  await Promise.resolve();
  assert.equal(replacement.get('bose-control-card'), card);
  assert.equal(replacement.get('ll-strategy-view-bose-control'), viewStrategy);
  assert.equal(replacement.get('ll-strategy-dashboard-bose-control'), dashboardStrategy);
}

testRegistrySwap().catch(error => { console.error(error); process.exitCode = 1; });

// Failure messages: distinct, exact, and free of forbidden instructions.
assert.equal(MESSAGES.fault_paused, "FAULTED — controls paused; use the P1 remote");
assert.equal(MESSAGES.uncertain, "We couldn't confirm that command. Check the room before pressing again.");
assert.equal(MESSAGES.not_sent, "That wasn't sent. Nothing was changed.");
assert.equal(MESSAGES.not_allowed, "That control is turned off.");
assert.equal(MESSAGES.connection_lost, "Can't reach the bridge computer right now.");
assert.equal(MESSAGES.refused_by_unit, "The Bose control unit declined that command. Nothing was changed.");
assert.equal(MESSAGES.config_uncertain, "The bridge's settings couldn't be confirmed. Ask the person who installed this system for help.");
assert.equal(new Set(Object.values(MESSAGES)).size, Object.keys(MESSAGES).length);
for (const text of Object.values(MESSAGES)) for (const word of FORBIDDEN) assert.ok(!text.toLowerCase().includes(word), word);

// Accordion: remembered room if it still exists, else the first room.
assert.equal(pickOpenRoom(['a', 'b', 'c'], 'b'), 'b');
assert.equal(pickOpenRoom(['a', 'b', 'c'], null), 'a');
assert.equal(pickOpenRoom(['a', 'b', 'c'], 'd'), 'a');
assert.equal(pickOpenRoom(['b', 'c'], 'a'), 'b');
assert.equal(pickOpenRoom([], 'a'), null);

// Relative time.
assert.equal(relativeTime(10), 'just now');
assert.equal(relativeTime(-5), 'just now');           // clock skew never shows the future
assert.equal(relativeTime(120), '2 min ago');
assert.equal(relativeTime(50 * 60), '50 min ago');
assert.equal(relativeTime(3 * 3600 + 10), '3 h ago');

// Last sent: only for the room that owns the action, only for real transmissions, honest about refusals.
const now = 1000000;
const sent = (action, outcome = 'transmitted-with-matching-response', ago = 120) => ({ action, outcome, observed_at: now - ago });
assert.equal(lastSent(sent('room-b-unmute'), 'b', now), 'Last sent: Unmute · 2 min ago');
assert.equal(lastSent(sent('room-b-unmute'), 'a', now), null);
assert.equal(lastSent(sent('volume-down'), 'a', now), 'Last sent: Volume down · 2 min ago');   // room A owns the legacy name
assert.equal(lastSent(sent('volume-down'), 'b', now), null);
assert.equal(lastSent(sent('room-c-source-video-1', undefined, 5), 'c', now), 'Last sent: TV · just now');
assert.equal(lastSent(sent('room-a-source-aux'), 'a', now), 'Last sent: Music · 2 min ago');
for (const bad of ['dry-run', 'rejected', 'uncertain']) assert.equal(lastSent(sent('room-b-mute', bad), 'b', now), null);
assert.equal(lastSent(null, 'a', now), null);
assert.equal(lastSent(sent('room-d-mute', 'refused-by-m1'), 'd', now), 'Room D: the Bose control unit declined the last command');
assert.equal(lastSent(sent('room-d-mute', 'refused-by-m1'), 'd', now, 'Garage'), 'Garage: the Bose control unit declined the last command');
assert.equal(MESSAGES.bridge_unavailable, "Bose control isn't available right now. Nothing was changed.");
assert.equal(lastSent(sent('room-d-mute', 'refused-by-m1'), 'b', now), null);
assert.equal(actionLabel('room-b-power-on'), 'On');
assert.equal(actionLabel('room-b-source-am'), 'AM');

// Controls only when the bridge reports serial-open.
assert.equal(controlsReady('serial-open'), true);
for (const state of ['unavailable', 'unknown', 'serial-closed', '', undefined]) assert.equal(controlsReady(state), false);

// Rendering.
const KEYS = ['power-on', 'power-off', 'volume-up', 'volume-down', 'mute', 'unmute', 'tv', 'music'];
const buttons = (disabled = false) => Object.fromEntries(KEYS.map(k => [k, { entity: `button.${k.replace('-', '_')}`, disabled }]));
let html = renderRoom({ id: 'b', name: 'Room B', open: true, caption: 'Last sent: Mute · 2 min ago', disabled: false, buttons: buttons() });
for (const label of ['On', 'Off', 'Volume up', 'Volume down', 'Mute', 'Unmute', 'TV', 'Music']) assert.ok(html.includes(`aria-label="Room B, ${label}"`), label);
assert.ok(html.includes('data-entity="button.volume_up"') && html.includes('aria-expanded="true"') && html.includes('Last sent: Mute'));
assert.ok(!/\bdisabled\b/.test(html), 'nothing disabled when ready');
assert.ok(!/selected|active|highlight/.test(html), 'no state or selection is ever shown');
html = renderRoom({ id: 'b', name: 'Room B', open: false, caption: null, disabled: false, buttons: buttons() });
assert.ok(html.includes('aria-expanded="false"') && !html.includes('data-key="mute"'), 'collapsed room is a header only');
html = renderRoom({ id: 'b', name: 'Room B', open: true, caption: null, disabled: true, buttons: buttons() });
assert.equal((html.match(/data-key="[a-z-]+"[^>]* disabled/g) || []).length, 8);
const partial = buttons(); partial.mute.disabled = true; delete partial.tv;
html = renderRoom({ id: 'b', name: 'Room B', open: true, caption: null, disabled: false, buttons: partial });
assert.equal((html.match(/data-key="[a-z-]+"[^>]* disabled/g) || []).length, 1);
assert.ok(!html.includes('aria-label="Room B, TV"'), 'an unregistered control is left out, never guessed');
html = renderRoom({ id: 'x', name: '<script>alert(1)</script> & "q"', open: true, caption: '<b>x</b>', disabled: false, buttons: buttons() });
assert.ok(!html.includes('<script>') && !html.includes('<b>x') && html.includes('&lt;script&gt;'), 'names and captions are escaped');
console.log('Card logic: accordion, last-sent filtering, refusal text, readiness and escaped rendering passed');
// ---- Controller with a stub hass: no DOM, never the real bridge ----
const KEYS8 = KEYS;
const layoutFor = (ids, names = { a: 'Room A', b: 'Room B', c: 'Room C', d: 'Room D' }) => ({
  health: 'sensor.health', outcome: 'sensor.outcome', manage_url: '/config/integrations/integration/bose_lifestyle',
  rooms: ids.map(id => ({ id, name: names[id], buttons: Object.fromEntries(KEYS8.map(k => [k, `button.${id}_${k}`])) })),
});
const SETUP_STUB = { needs_setup: false, mapping: 'Rooms A to D are the four room outputs on the Bose control unit.', rooms: [],
  sources: [['am', 'AM radio', true], ['fm', 'FM radio', false], ['cd', 'CD player', false], ['aux', 'Auxiliary input', true],
    ['video-1', 'Television (Video 1)', true], ['video-2', 'Video 2', true], ['tape', 'Tape deck', false]].map(([source, label, connected]) => ({ source, label, connected })) };
function makeHass({ admin = false, health = 'serial-open', layout = layoutFor(['a', 'b', 'c']), outcome = null, failWS = null, failPress = false, experimental = [] } = {}) {
  const calls = { ws: [], services: [] };
  let irZone = layout.ir_target ? layout.ir_target.zone : null;   // the IR target the bridge reports; set_ir_zone changes it
  let irLabel = layout.ir_receiver_label === undefined ? null : layout.ir_receiver_label;
  const states = { 'sensor.health': { state: health, attributes: {} }, 'sensor.outcome': { state: 'x', attributes: outcome || {} } };
  for (const room of layout.rooms) for (const e of Object.values(room.buttons)) states[e] = { state: 'unknown', attributes: {} };
  let settings = ['a', 'b', 'c', 'd'].map(id => ({ id, name: { a: 'Room A', b: 'Room B', c: 'Room C', d: 'Room D' }[id], enabled: id !== 'd', live: id !== 'd', commissioning: id === 'd' ? 'No zone installed, commands inferred' : 'Commissioned', experimental: experimental.includes(id) }));
  let bridgeName = layout.bridge_name || 'Bose Controller';   // Task 8c: the nickname the bridge reports
  const hass = {
    user: { is_admin: admin }, states, calls,
    async callWS(msg) {
      calls.ws.push(msg);
      const failure = failWS && failWS(msg);
      if (failure) { const error = new Error('boom'); if (typeof failure === 'string') error.code = failure; throw error; }   // a string names the bridge's code
      if (msg.type === 'bose_lifestyle/layout') return { ...layout, bridge_name: bridgeName, ...(layout.ir_target ? { ir_target: { ...layout.ir_target, zone: irZone } } : {}), ir_receiver_label: irLabel };
      if (msg.type === 'bose_lifestyle/set_ir_zone') { irZone = msg.zone; return { ir_target: { ...(layout.ir_target || {}), zone: irZone }, message: 'The last-commanded mute state is forgotten for the new target room.' }; }
      if (msg.type === 'bose_lifestyle/set_ir_receiver_label') { irLabel = msg.label === null || msg.label === undefined ? null : msg.label; return { ir_receiver_label: irLabel }; }
      if (msg.type === 'bose_lifestyle/settings') return { rooms: settings, setup: SETUP_STUB, bridge_name: bridgeName };
      if (msg.type === 'bose_lifestyle/set_bridge_name') { bridgeName = msg.name === null ? 'Bose Controller' : msg.name; return { rooms: settings, setup: SETUP_STUB, bridge_name: bridgeName }; }
      if (msg.type === 'bose_lifestyle/set_room_name') { settings = settings.map(r => r.id === msg.room ? { ...r, name: msg.name } : r); return { rooms: settings, setup: SETUP_STUB }; }
      if (msg.type === 'bose_lifestyle/set_experimental') { settings = settings.map(r => r.id === msg.room ? { ...r, experimental: msg.allow } : r); return { rooms: settings, setup: SETUP_STUB }; }
      if (msg.type === 'bose_lifestyle/set_source') return { rooms: settings, setup: SETUP_STUB };
      if (msg.type === 'bose_lifestyle/set_volume_steps') { layout.volume_steps = msg.steps; return { volume_steps: msg.steps }; }
      if (msg.type === 'bose_lifestyle/set_room') {
        settings = settings.map(r => r.id === msg.room ? { ...r, ...(msg.name !== undefined ? { name: msg.name } : {}), ...(msg.enabled !== undefined ? { enabled: msg.enabled, live: msg.room === 'd' ? msg.enabled : r.live } : {}) } : r);
        return { rooms: settings };
      }
      if (msg.type === 'bose_lifestyle/events') return [{ t: 1, kind: 'tx', action: 'volume-down', detail: 'first' }, { t: 2, kind: 'outcome', action: 'volume-down', detail: 'last' }];
      throw new Error('unexpected ' + msg.type);
    },
    async callService(domain, service, data) {
      calls.services.push([domain, service, data]);
      if (failPress) {
    const error = new Error(typeof failPress === 'string' ? globalThis.BoseCard.MESSAGES[failPress] : 'press failed');
    if (typeof failPress === 'string') error.translation_key = failPress;
    throw error;
  }
    },
  };
  return hass;
}
const memory = () => { const m = new Map(); return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), m }; };
const make = (hass, storage = memory()) => {
  const buzz = [];
  const c = new Controller({ storage, haptic: kind => buzz.push(kind), now: () => 1000 * 1000, onChange: () => {} });
  c.setHass(hass);
  return { c, storage, buzz };
};
const settle = () => new Promise(r => setImmediate(r));

(async () => {
  // Layout is fetched once; the first room is open by default; no calls happen without a tap.
  let hass = makeHass();
  let { c, storage, buzz } = make(hass);
  await settle();
  c.setHass(hass); c.setHass(hass);
  assert.deepEqual(hass.calls.ws.map(m => m.type), ['bose_lifestyle/layout']);
  let view = c.view();
  assert.deepEqual(view.rooms.map(r => [r.id, r.open]), [['a', true], ['b', false], ['c', false]]);
  assert.equal(view.admin, false); assert.equal(view.dim, false); assert.equal(view.message, null);
  assert.equal(hass.calls.services.length, 0);

  // Accordion remembers the last opened room per device; storage failures are ignored.
  c.toggleRoom('b');
  assert.deepEqual(c.view().rooms.map(r => r.open), [false, true, false]);
  assert.equal(storage.m.get('bose_lifestyle.open_room'), 'b');
  c.toggleRoom('b');
  assert.deepEqual(c.view().rooms.map(r => r.open), [false, false, false]);
  assert.equal(storage.m.get('bose_lifestyle.open_room'), 'b');
  ({ c } = make(hass, storage)); await settle();
  assert.equal(c.view().rooms.find(r => r.open).id, 'b');
  const throwing = { getItem() { throw new Error('private mode'); }, setItem() { throw new Error('private mode'); } };
  ({ c } = make(hass, throwing)); await settle();
  assert.equal(c.view().rooms.find(r => r.open).id, 'a'); c.toggleRoom('c');
  assert.equal(c.view().rooms.find(r => r.open).id, 'c');

  // Taps: one service call per tap, rapid taps are not suppressed here (the bridge paces), a failure is reported and never retried.
  ({ c, buzz } = make(hass)); await settle();
  hass.calls.services.length = 0;
  for (let i = 0; i < 3; i++) await c.tap('button.a_volume-up');
  assert.deepEqual(hass.calls.services, Array(3).fill(['button', 'press', { entity_id: 'button.a_volume-up' }]));
  assert.deepEqual(buzz, ['light', 'light', 'light']);
  const failing = makeHass({ failPress: true });
  ({ c } = make(failing)); await settle();
  await c.tap('button.a_mute');
  assert.equal(failing.calls.services.length, 1);
  assert.equal(c.view().notice, MESSAGES.uncertain);   // an unknown press failure is never called a plain failure

  // Rate limited: "Not sent" on the pressed control only, disabled for 1.5 s, nothing queued or repeated.
  let t = 0;
  const limited = makeHass({ failPress: 'not_sent' });
  c = new Controller({ storage: memory(), haptic() {}, now: () => t, onChange: () => {} });
  c.setHass(limited); await settle();
  await c.tap('button.a_mute');
  await c.tap('button.a_mute');                         // blocked: not sent again and not queued
  assert.equal(limited.calls.services.length, 1);
  assert.equal(c.view().notice, MESSAGES.not_sent);
  let rowA = c.view().rooms[0].buttons;
  assert.equal(rowA.mute.disabled, true); assert.equal(rowA.mute.flag, 'Not sent');
  assert.equal(rowA['volume-up'].disabled, false); assert.equal(rowA['volume-up'].flag, null);
  t = 1499; assert.equal(c.view().rooms[0].buttons.mute.disabled, true);
  await c.tap('button.a_volume-up');                    // the volume path is not held by the mute block
  assert.equal(limited.calls.services.length, 2);
  t = 1500; assert.equal(c.view().rooms[0].buttons.mute.disabled, false);
  await c.tap('button.a_mute');                         // only after the block ends
  assert.equal(limited.calls.services.length, 3);

  // Quick volume taps: a press the bridge drops for pacing is silent and never blocks the control.
  {
    const quick = makeHass({ failPress: 'not_sent' });
    let clock = 0;
    const cq = new Controller({ storage: memory(), haptic() {}, now: () => clock, onChange: () => {} });
    cq.setHass(quick); await settle();
    await cq.tap('button.a_volume-up', 'a', 'volume-up');
    assert.equal(cq.view().notice, null);                       // no alert for a paced-out volume press
    assert.equal(cq.view().rooms[0].buttons['volume-up'].disabled, false);
    assert.equal(cq.view().rooms[0].buttons['volume-up'].flag, null);
    await cq.tap('button.a_volume-up', 'a', 'volume-up');       // the next tap goes straight through
    await cq.tap('button.a_volume-down', 'a', 'volume-down');
    assert.equal(quick.calls.services.length, 3);               // nothing queued, nothing blocked, every tap sent once
    await cq.tap('button.a_mute', 'a', 'mute');                 // other controls keep the visible Not sent behavior
    assert.equal(cq.view().notice, MESSAGES.not_sent);
    assert.equal(cq.view().rooms[0].buttons.mute.disabled, true);
  }

  // Dashboard timing: volume taps are never held back; inputs and power wait out a 2.5 s cooldown; a held volume key repeats.
  {
    let clock = 0;
    const hass2 = makeHass({});
    const q = new Controller({ storage: memory(), haptic() {}, now: () => clock, onChange: () => {} });
    q.setHass(hass2); await settle();
    for (let i = 0; i < 5; i++) await q.tap('button.a_volume_up', 'a', 'volume-up');   // rapid volume taps, same instant
    assert.equal(hass2.calls.services.length, 5, 'every rapid volume tap is sent');
    await q.tap('button.a_source_cd', 'a', 'source-cd');
    await q.tap('button.b_power_on', 'b', 'power-on');                                  // inside the cooldown
    assert.equal(hass2.calls.services.length, 6, 'a second input or power change within 2.5 s is refused');
    assert.equal(q.view().rooms[1].buttons['power-on'].disabled, true, 'and shown disabled while it cools down');
    clock = 2600;
    await q.tap('button.b_power_on', 'b', 'power-on');
    assert.equal(hass2.calls.services.length, 7, 'after the cooldown the next power change goes through');
    await q.tap('button.a_mute', 'a', 'mute');
    await q.tap('button.a_mute', 'a', 'mute');
    assert.equal(hass2.calls.services.length, 9, 'mute is one press per tap, no repeat');
  }
  // The tiles re-enable themselves when the cooldown ends, with no HA update needed to trigger a render.
  {
    let changes = 0;
    const hass3 = makeHass({});
    const q = new Controller({ storage: memory(), haptic() {}, now: () => 0, onChange: () => { changes += 1; } });
    q.setHass(hass3); await settle();
    await q.tap('button.a_source_cd', 'a', 'source-cd');
    const afterTap = changes;
    await new Promise(resolve => setTimeout(resolve, 2700));
    assert.ok(changes > afterTap, 'a render is scheduled for the end of the cooldown');
  }
  // A held volume key: the click that follows the release is judged from the release time, never adding a step.
  assert.ok(/stopHold\(\) \{[^}]*volumePointerAt = this\.controller\.now\(\)/.test(require('fs').readFileSync(require('path').join(__dirname, '..', 'custom_components/bose_lifestyle/frontend/bose-control-card.js'), 'utf8')), 'the click guard restarts at pointer release');
  // Hold-to-repeat exists for volume only (the pointer timers run in the shadow root).
  const cardSource = require('fs').readFileSync(require('path').join(__dirname, '..', 'custom_components/bose_lifestyle/frontend/bose-control-card.js'), 'utf8');
  assert.ok(/REPEAT_KEYS\s*=\s*new Set\(\['volume-up', 'volume-down'\]\)/.test(cardSource), 'hold repeat is limited to the volume keys');
  assert.ok(/HOLD_REPEAT_MS\s*=\s*300/.test(cardSource), 'repeat interval stays at or above the bridge pacing');
  console.log('ok dashboard timing: volume taps, input and power cooldown, mute, hold repeat');

  // Unknown failure classes from the bridge: fault and config text come from the view, not from a press.
  hass = makeHass(); c = new Controller({ storage: memory(), haptic() {}, now: () => 0, onChange: () => {} });
  c.setHass(hass); await settle();
  hass.states['sensor.health'] = { state: 'serial-open', attributes: { fault: 'latched', config: 'config-uncertain', fault_message: '<img src=x onerror=alert(1)>' } };
  assert.equal(c.view().message, MESSAGES.fault_paused);   // fault wins over the config message
  assert.equal(c.view().faultMessage, '<img src=x onerror=alert(1)>');
  const { renderMain } = globalThis.BoseCard;
  let faultHtml = renderMain(c.view());
  assert.ok(faultHtml.includes('&lt;img src=x onerror=alert(1)&gt;') && !faultHtml.includes('<img'), 'fault supplement is escaped');
  hass.states['sensor.health'] = { state: 'serial-open', attributes: { fault: null, config: 'config-uncertain' } };
  assert.equal(c.view().message, MESSAGES.config_uncertain);
  assert.equal(c.view().faultMessage, null, 'fault supplement is hidden after the latch clears');
  assert.notEqual(c.view().message, MESSAGES.uncertain);
  hass.states['sensor.health'] = { state: 'serial-open', attributes: { fault: null, config: 'ok' } };
  assert.equal(c.view().message, null);
  // A press refused for a fault reports the fault wording.
  const faulted = makeHass({ failPress: 'fault_paused' });
  ({ c } = make(faulted)); await settle();
  await c.tap('button.a_mute');
  assert.equal(c.view().notice, MESSAGES.fault_paused);
  // Admin help stays reachable while the bridge is down.
  const down = makeHass({ admin: true, failWS: m => m.type === 'bose_lifestyle/layout' });
  ({ c } = make(down)); await settle();
  assert.equal(c.view().admin, true);

  // Captions: only the room that owns the last outcome, only after a real transmission.
  hass = makeHass({ outcome: { action: 'room-b-mute', outcome: 'transmitted-with-matching-response', observed_at: 880 } });
  ({ c } = make(hass)); await settle();
  assert.deepEqual(c.view().rooms.map(r => r.caption), [null, 'Last sent: Mute · 2 min ago', null]);

  // Not ready: bridge down dims everything; a failed layout shows the ready message with no controls.
  hass = makeHass({ health: 'unavailable' });
  ({ c } = make(hass)); await settle();
  view = c.view();
  assert.equal(view.dim, true); assert.equal(view.message, 'Sound controls are unavailable. Use the Bose remote.');
  assert.ok(view.rooms.every(r => r.disabled));
  hass = makeHass({ failWS: m => m.type === 'bose_lifestyle/layout' });
  ({ c } = make(hass)); await settle();
  view = c.view();
  assert.equal(view.message, "Bose Lifestyle Assistant isn't ready"); assert.deepEqual(view.rooms, []);

  // Unregistered or unavailable entities are disabled individually.
  hass = makeHass(); hass.states['button.a_mute'] = { state: 'unavailable', attributes: {} }; delete hass.states['button.a_unmute'];
  ({ c } = make(hass)); await settle();
  const a = c.view().rooms[0].buttons;
  assert.equal(a.mute.disabled, true); assert.equal(a.unmute.disabled, true); assert.equal(a['volume-up'].disabled, false);

  // Non-admin: no gear, no settings or events calls, even if asked.
  hass = makeHass({ admin: false });
  ({ c } = make(hass)); await settle();
  await c.openSettings();
  assert.equal(c.view().admin, false); assert.equal(c.view().sheet, null);
  assert.deepEqual(hass.calls.ws.map(m => m.type), ['bose_lifestyle/layout']);

  // Admin: settings sheet loads settings and the last 20 events; Room D needs the exact confirmation first.
  hass = makeHass({ admin: true });
  ({ c } = make(hass)); await settle();
  assert.equal(c.view().admin, true);
  await c.openSettings();
  assert.deepEqual(hass.calls.ws.slice(1).map(m => m.type), ['bose_lifestyle/settings', 'bose_lifestyle/events']);
  assert.equal(hass.calls.ws[2].limit, 20);
  let sheet = c.view().sheet;
  assert.deepEqual(sheet.rooms.map(r => [r.id, r.enabled]), [['a', true], ['b', true], ['c', true], ['d', false]]);
  assert.deepEqual(sheet.events.map(e => e.detail), ['last', 'first']);   // newest first
  const before = hass.calls.ws.length;
  await c.setEnabled('d', true);
  assert.equal(c.view().sheet.error, 'Read and confirm the Experimental section first.');   // no ungated route to Room D
  assert.equal(hass.calls.ws.length, before);
  c.ack(true);
  await c.setEnabled('d', true);
  assert.equal(hass.calls.ws.length, before);                              // nothing sent yet
  assert.equal(c.view().sheet.confirm.text, CONFIRM_D);
  assert.equal(CONFIRM_D, "Room D commands have never been tested on the Bose control unit. Only turn this on once a Room D zone is connected. The Bose control unit may refuse them, and an unexpected or missing reply stops all Bose control until someone investigates. Enable Room D?");
  await c.confirm(false);
  assert.equal(hass.calls.ws.length, before); assert.equal(c.view().sheet.confirm, null);
  await c.setEnabled('d', true); await c.confirm(true);
  assert.deepEqual(hass.calls.ws.filter(m => m.type === 'bose_lifestyle/set_room'), [{ type: 'bose_lifestyle/set_room', room: 'd', enabled: true }]);
  assert.equal(c.view().sheet.rooms[3].enabled, true);
  assert.equal(hass.calls.ws.filter(m => m.type === 'bose_lifestyle/layout').length, 2);   // layout reloaded
  await c.setEnabled('d', false);                                          // turning it off needs no confirmation
  assert.deepEqual(hass.calls.ws.filter(m => m.type === 'bose_lifestyle/set_room').pop(), { type: 'bose_lifestyle/set_room', room: 'd', enabled: false });
  await c.setName('b', ' Garden ');
  assert.deepEqual(hass.calls.ws.filter(m => m.type === 'bose_lifestyle/set_room_name').pop(), { type: 'bose_lifestyle/set_room_name', room: 'b', name: 'Garden' });

  // A failed Room D change leaves the switch where the server says, and says so.
  hass = makeHass({ admin: true, failWS: m => m.type === 'bose_lifestyle/set_room' && 'invalid_request' });
  ({ c } = make(hass)); await settle(); await c.openSettings(); c.ack(true);
  await c.setEnabled('d', true); await c.confirm(true);
  sheet = c.view().sheet;
  assert.equal(sheet.error, "That change wasn't saved. Nothing was changed.");   // the bridge named a definite refusal
  assert.equal(sheet.rooms[3].enabled, false);
  assert.equal(hass.calls.ws.filter(m => m.type === 'bose_lifestyle/set_room').length, 1);   // no retry
  assert.ok(hass.calls.ws.filter(m => m.type === 'bose_lifestyle/settings').length >= 2);   // re-read the server's value

  // An unexplained failure may have reached the bridge: say so and never claim nothing changed.
  hass = makeHass({ admin: true, failWS: m => m.type === 'bose_lifestyle/set_room' });
  ({ c } = make(hass)); await settle(); await c.openSettings(); c.ack(true);
  await c.setEnabled('d', true); await c.confirm(true);
  sheet = c.view().sheet;
  assert.equal(sheet.error, "We couldn't confirm that change. Check the settings before changing them again.");
  assert.equal(sheet.rooms[3].enabled, false);
  assert.equal(hass.calls.ws.filter(m => m.type === 'bose_lifestyle/set_room').length, 1);   // still no retry

  // Settings rendering escapes names and shows the confirmation and events.
  let html = renderSettings({ view: 'setup', rooms: [{ id: 'a', name: '<i>x</i>', enabled: true, live: true, commissioning: 'Commissioned' }, { id: 'd', name: 'Room D', enabled: false, live: false, commissioning: 'No zone installed, commands inferred' }],
    events: [{ t: 5, kind: 'fault', action: null, detail: '<b>bad</b>' }], confirm: { room: 'd', text: CONFIRM_D }, error: null, fault: 'partial-write', manage_url: '/config/integrations/integration/bose_lifestyle', sources: [], mapping: '', acks: { d: true } });
  assert.ok(!html.includes('<i>x') && !html.includes('<b>bad') && html.includes('&lt;i&gt;x'));
  assert.ok(!html.includes('No zone installed') && !html.includes('Commissioned') && html.includes('data-act="confirm-yes"') && html.includes('data-act="confirm-no"'));
  assert.ok(!html.includes('partial-write') && !html.includes('/config/logs'), 'diagnostics belong to the main settings view');
  html = renderSettings({ view: 'rooms', rooms: [], events: [], confirm: null, error: null, fault: 'partial-write', manage_url: '/config/integrations/integration/bose_lifestyle' });
  assert.ok(html.includes('partial-write') && html.includes('/config/logs?filter=bose_lifestyle') && html.includes('/config/integrations/integration/bose_lifestyle') && html.includes('data-act="setup"'));

  // Diagnostics: "no recent events" is not "couldn't load"; raw events sit inside Technical details.
  html = renderRoom({ id: 'b', name: 'Room B', open: true, caption: null, disabled: false, buttons: { ...buttons(), mute: { entity: 'button.b_mute', disabled: true, reason: 'zone-disabled' } } });
  assert.ok(html.includes('This room is turned off in settings') && html.includes('class="why"'), 'unavailable reason is visible text, not only a title');
  html = renderSettings({ view: 'rooms', rooms: [], events: [], confirm: null, error: null, fault: null });
  assert.ok(html.includes('No recent events.') && !html.includes("Couldn't load diagnostics"));
  html = renderSettings({ view: 'rooms', rooms: [], events: null, confirm: null, error: null, fault: null });
  assert.ok(html.includes("Couldn't load diagnostics.") && !html.includes('No recent events'));
  html = renderSettings({ view: 'rooms', rooms: [], events: [{ t: 5, kind: 'tx', action: 'volume-down', detail: '4d' }], confirm: null, error: null, fault: null });
  assert.ok(html.indexOf('Technical details') < html.indexOf('<b>tx</b>') && html.indexOf('<details') < html.indexOf('<b>tx</b>'), 'raw events only inside a collapsed Technical details');

  // A failed layout is retried (read-only) every 30 s of updates, never on every update, and stops once it works.
  let clock = 0; const asked = [];
  let flaky = makeHass({ failWS: m => { asked.push(m.type); return asked.length === 1; } });   // first layout call fails, the next succeeds
  c = new Controller({ storage: memory(), haptic() {}, now: () => clock, onChange: () => {} });
  c.setHass(flaky); await settle();
  assert.equal(c.view().message, "Bose Lifestyle Assistant isn't ready");
  clock = 10000; c.setHass(flaky); await settle();
  assert.equal(asked.length, 1);
  clock = 31000; c.setHass(flaky); await settle();
  assert.equal(asked.length, 2); assert.equal(c.view().message, null);
  clock = 50000; c.setHass(flaky); await settle();
  assert.equal(asked.length, 2);                                           // under 30 s since the last read: no request
  clock = 90000; c.setHass(flaky); await settle();
  assert.equal(asked.length, 3);                                           // a working layout is re-read read-only every 30 s (other clients' nicknames)
  clock = 100000; c.setHass(flaky); await settle();
  assert.equal(asked.length, 3);                                           // and never on every update

  // An empty layout is also "not ready", and an administrator still gets the settings icon to fix it.
  hass = makeHass({ admin: true, layout: layoutFor([]) });
  ({ c } = make(hass)); await settle();
  view = c.view();
  assert.equal(view.message, "Bose Lifestyle Assistant isn't ready"); assert.deepEqual(view.rooms, []); assert.equal(view.admin, true);

  // Changes made by the card itself always re-render; a state update never rebuilds under typing or when nothing changed.
  const reasons = [];
  c = new Controller({ storage: memory(), haptic() {}, now: () => 0, onChange: reason => reasons.push(reason) });
  hass = makeHass();
  c.setHass(hass); await settle(); c.setHass(hass); await c.tap('button.a_mute');
  assert.ok(reasons.includes('hass') && reasons.includes(undefined));
  assert.equal(reasons[reasons.length - 1], undefined);
  assert.equal(shouldRender({ sheet: false, reason: 'hass', html: 'x', last: 'x', typing: false }), false);
  assert.equal(shouldRender({ sheet: false, reason: 'hass', html: 'y', last: 'x', typing: false }), true);
  assert.equal(shouldRender({ sheet: true, reason: 'hass', html: 'x', last: 'x', typing: false }), false);
  assert.equal(shouldRender({ sheet: true, reason: 'hass', html: 'y', last: 'x', typing: true }), false);
  assert.equal(shouldRender({ sheet: true, reason: 'hass', html: 'y', last: 'x', typing: false }), true);
  assert.equal(shouldRender({ sheet: true, reason: undefined, html: 'x', last: 'x', typing: true }), true);   // the card's own change always shows

  // Nothing unknown reaches an attribute: unknown control keys are dropped, links must be local paths.
  html = renderRoom({ id: 'b', name: 'Room B', open: true, caption: null, disabled: false, buttons: { ...buttons(), 'x" onclick="evil': { entity: 'button.z', disabled: false } } });
  assert.ok(!html.includes('onclick') && !html.includes('button.z'));
  html = renderSettings({ view: 'rooms', rooms: [], events: [], confirm: null, error: null, fault: null, manage_url: 'javascript:alert(1)' });
  assert.ok(!html.includes('javascript:') && html.includes('/config/integrations/integration/bose_lifestyle'));

  // Event times are local, in the list and in the copied text.
  const stamp = new Date(5000 * 1000);
  html = renderSettings({ view: 'rooms', rooms: [], events: [{ t: 5000, kind: 'tx', action: null, detail: 'd' }], confirm: null, error: null, fault: null });
  assert.ok(html.includes(stamp.toLocaleTimeString()));
  assert.ok(eventsText([{ t: 5000, kind: 'tx', action: null, detail: 'd' }]).includes(stamp.toLocaleString()));

  // Generated view: one view with the card.
  assert.deepEqual(generateView(), { title: 'Bose Lifestyle Assistant', cards: [{ type: 'custom:bose-control-card' }] });
  assert.deepEqual(generateDashboard(), { views: [{ title: 'Bose Lifestyle Assistant', path: 'bose', icon: 'mdi:speaker', cards: [{ type: 'custom:bose-control-card' }] }] });
  console.log('Card controller: layout once, accordion memory, taps, captions, readiness, admin settings, Room D confirmation and failure handling passed');
})().catch(error => { console.error(error); process.exit(1); });

// ---- Task 10b: disabled rooms, unavailable reasons and the IR target line ----
const { irLine, REASON_TEXT } = globalThis.BoseCard;
(() => {
  const d = Object.fromEntries(KEYS.map(k => [k, { entity: `button.d_${k.replace('-', '_')}`, disabled: true }]));
  d.mute = { entity: 'button.d_mute', disabled: true, reason: 'zone-disabled' };
  d.tv = { entity: 'button.d_tv', disabled: true, reason: 'fault' };
  let html = renderRoom({ id: 'd', name: 'Room D', open: true, caption: 'Turned off in settings', disabled: true, buttons: d });
  assert.ok(html.includes('Turned off in settings') && (html.match(/data-key="[a-z-]+"[^>]* disabled/g) || []).length === 8);
  assert.ok(html.includes(`title="${REASON_TEXT['zone-disabled']}"`) && html.includes(`title="${REASON_TEXT.fault}"`), 'reasons are shown on the control');
  assert.ok(!html.includes('title="undefined"') && !html.includes('title=""'), 'a control without a reason has no title');
  assert.equal(Object.keys(REASON_TEXT).sort().join(','), 'experimental-off,fault,not-live,source-not-connected,zone-disabled');
  html = renderRoom({ id: 'b', name: 'Room B', open: true, caption: null, disabled: false, buttons: { source_x: { entity: 'button.x', disabled: false, reason: '<b>' } } });
  assert.ok(!html.includes('<b>') && !html.includes('title="<b>"'), 'an unknown reason is never written into the page');
  html = renderRoom({ id: 'b', name: 'Room B', open: true, caption: null, disabled: false, buttons: { 'source-am': { entity: 'button.b_am', disabled: false }, tv: { entity: 'button.b_tv', disabled: false } } });
  assert.ok(html.includes('data-key="source-am"') && html.includes('aria-label="Room B, AM"'), 'connected extra sources render with their label');
  assert.ok(!html.includes('undefined'), 'no undefined text or icon for a source button');

  // IR target line: always names the room; says why when the remote cannot control it; nothing when there is no target.
  assert.equal(irLine(null), '');
  let line = irLine({ zone: 'b', zone_name: 'Room B', allowed: true, reason: null });
  assert.ok(line.includes('This remote controls Room B') && !line.includes('warn'));
  line = irLine({ zone: 'd', zone_name: 'Room D', allowed: false, reason: 'zone-disabled' });
  assert.ok(line.includes('This remote controls Room D') && line.includes('warn') && line.includes('Turn this room on in settings'));
  line = irLine({ zone: null, zone_name: null, allowed: false, reason: 'config-invalid' });
  assert.ok(line.includes('Settings are invalid') && !line.includes('This remote controls'));
  line = irLine({ zone: 'c', zone_name: '<i>x</i>', allowed: false, reason: 'experimental-off' });
  assert.ok(!line.includes('<i>') && line.includes('&lt;i&gt;x'), 'names are escaped');
  line = irLine({ zone: 'a', zone_name: 'Room A', allowed: false, reason: 'fault' });
  assert.ok(line.includes('paused until the bridge is checked') && line.includes('warn'));
  line = irLine({ zone: 'a', zone_name: 'Room A', allowed: false, reason: 'not-live' });
  assert.ok(line.includes('not available right now') && !line.includes('cannot control this room'));
  console.log('Card layout fields: disabled rooms, reason titles and the IR target line passed');
})();

(async () => {
  const layoutV4 = { health: 'sensor.health', outcome: 'sensor.outcome', manage_url: '/config/integrations/integration/bose_lifestyle',
    ir_target: { zone: 'd', zone_name: 'Room D', allowed: false, reason: 'zone-disabled' },
    rooms: ['a', 'b', 'c', 'd'].map(id => ({ id, name: { a: 'Room A', b: 'Room B', c: 'Room C', d: 'Room D' }[id], enabled: id !== 'd',
      buttons: Object.fromEntries(KEYS8.map(k => [k, `button.${id}_${k}`])), unavailable: id === 'a' ? { mute: 'fault' } : {} })) };
  let hass = makeHass({ layout: layoutV4 });
  let { c } = make(hass); await settle();
  let view = c.view();
  assert.deepEqual(view.irTarget, layoutV4.ir_target);
  assert.deepEqual(view.rooms.map(r => r.id), ['a','b','c']);
  assert.equal(view.rooms[0].disabled, false); assert.equal(view.rooms[0].buttons.mute.reason, 'fault'); assert.equal(view.rooms[0].buttons['volume-up'].reason, null);
  // Room D change refused by the server: its own message is shown; the switch shows what the server says.
  hass = makeHass({ admin: true });
  const callWS = hass.callWS;
  hass.callWS = async msg => { if (msg.type === 'bose_lifestyle/set_room') throw { code: 'partial_failure', message: 'Room D changed partly; nothing was retried.' }; return callWS(msg); };
  ({ c } = make(hass)); await settle(); await c.openSettings(); c.ack(true);
  await c.setEnabled('d', true); await c.confirm(true);
  assert.equal(c.view().sheet.error, 'Room D changed partly; nothing was retried.');
  console.log('Card controller: disabled rooms, unavailable reasons, IR target and partial-failure message passed');
})().catch(error => { console.error(error); process.exit(1); });

// ---- Task 8: admin setup screen, Finish setup, experimental gating, save failures ----
(async () => {
  const { renderMain, renderSettings: settingsHtml, renderRoom: roomHtml, MESSAGES: M } = globalThis.BoseCard;
  const FORBIDDEN8 = ['restart', 'reboot', 'unplug', 'power cycle', 'try again', 'retry'];
  const EXPLAIN = 'Room D commands have never been tested on the Bose control unit.';
  const fresh = { ...layoutFor(['a', 'b', 'c', 'd']), needs_setup: true };

  // test_fresh_install_shows_finish_setup_for_admin_and_neutral_message_for_others
  let hass = makeHass({ admin: true, layout: fresh });
  let { c } = make(hass); await settle();
  let html = renderMain(c.view());
  assert.ok(html.includes('Unofficial community project. Not affiliated with Bose Corporation.'), 'the rendered card carries the non-affiliation statement');
  assert.ok(html.includes('data-act="setup"') && html.includes('Finish setup'));
  assert.ok(!/\bPi\b/.test(html));
  hass = makeHass({ admin: false, layout: fresh });
  ({ c } = make(hass)); await settle();
  html = renderMain(c.view());
  assert.ok(html.includes('Not set up yet. Ask the person who installed this system.'));
  assert.ok(!html.includes('Finish setup') && !html.includes('data-act="setup"'), 'non-admins never see the setup link');
  for (const word of FORBIDDEN8) assert.ok(!html.toLowerCase().includes(word), word);
  hass = makeHass({ admin: true });   // configured: no Finish setup
  ({ c } = make(hass)); await settle();
  assert.ok(!renderMain(c.view()).includes('Finish setup'));
  assert.ok(!renderMain({ ...c.view(), irTarget: { zone: 'b', zone_name: 'Room B', allowed: true, reason: null } }).includes('This remote controls'), 'the daily card does not show the infrared target');

  // test_daily_controls_hide_unconnected_sources: the room renders exactly the buttons it was given.
  html = roomHtml({ id: 'a', name: 'Room A', open: true, caption: null, disabled: false,
    buttons: { tv: { entity: 'button.a_tv', disabled: false }, music: { entity: 'button.a_music', disabled: false } } });
  assert.ok(html.includes('aria-label="Room A, TV"') && !html.includes('aria-label="Room A, CD"') && !html.includes('data-key="source-cd"'));

  // test_source_labels_are_the_familiar_names (card side): every jack with its familiar label, the mapping sentence and the enable wording.
  hass = makeHass({ admin: true });
  ({ c } = make(hass)); await settle();
  await c.openSetup();
  assert.equal(c.view().sheet.view, 'setup');
  html = settingsHtml(c.view().sheet);
  for (const label of ['AM radio', 'FM radio', 'CD player', 'Auxiliary input', 'Television (Video 1)', 'Video 2', 'Tape deck']) assert.ok(html.includes(label), label);
  assert.ok(html.includes('Rooms A to D are the four room outputs on the Bose control unit.') && html.includes('Enable app control for this room'));
  assert.ok(html.includes('maxlength="40"') && html.includes('class="rname"'));
  assert.ok(html.includes('Room A') && html.includes('Room C'));

  // test_experimental_switch_requires_explanation_first: the explanation and the I understand box come before Room D's switch, which is locked until ticked.
  const explainAt = html.indexOf(EXPLAIN), ackAt = html.indexOf('data-act="ack" data-room="d"'), dAt = html.indexOf('data-act="enable" data-room="d"');
  assert.ok(explainAt > 0 && explainAt < ackAt && ackAt < dAt, 'explanation, then the checkbox, then the switch');
  assert.ok(/data-act="enable" data-room="d"[^>]*disabled/.test(html), 'Room D switch is locked before the checkbox');
  assert.ok(!/data-act="enable" data-room="a"[^>]*disabled/.test(html), 'commissioned rooms are not locked');
  const sent = hass.calls.ws.length;
  await c.setEnabled('d', true);
  assert.equal(hass.calls.ws.length, sent);
  assert.equal(c.view().sheet.error, 'Read and confirm the Experimental section first.');
  c.ack(true);
  html = settingsHtml(c.view().sheet);
  assert.ok(/data-act="enable" data-room="d"(?![^>]*disabled)/.test(html), 'unlocked after the checkbox');

  // test_setup_uncertain_commit_shown_not_retried: not_saved says nothing changed; uncertain is the standard text and is never retried.
  let sourceCalls = 0;
  const base = hass.callWS;
  hass.callWS = async msg => {
    if (msg.type === 'bose_lifestyle/set_source') {
      sourceCalls++;
      throw sourceCalls === 1 ? { code: 'not_saved', message: 'not saved' } : { code: 'uncertain', message: 'uncertain' };
    }
    return base(msg);
  };
  await c.setSource('tape', true);
  assert.equal(c.view().sheet.error, 'Nothing was changed. The settings could not be saved.');
  await c.setSource('tape', true);
  assert.equal(sourceCalls, 2);
  assert.equal(c.view().sheet.error, "We couldn't confirm that change. Check the settings before changing them again.");
  html = settingsHtml(c.view().sheet);
  assert.ok(!/data-act="retry"|Try again|try again|retry/i.test(html), 'no retry control after an uncertain commit');
  assert.ok(html.includes("We couldn't confirm that change. Check the settings before changing them again.".replace(/'/g, '&#39;')), 'standard uncertain text is shown, escaped');
  await settle();
  assert.equal(sourceCalls, 2, 'nothing retried by itself');
  hass.callWS = async msg => { if (msg.type === 'bose_lifestyle/set_source') throw { code: 'bridge_error', message: 'x' }; return base(msg); };
  await c.setSource('tape', true);
  assert.equal(c.view().sheet.error, "We couldn't confirm that change. Check the settings before changing them again.", 'an unclear failure is never reported as definitely not saved');
  hass.callWS = base;
  // Room D: locked to turning on only; turning it off stays possible, and the visible wording is present.
  c.sheet.rooms[3].enabled = true; c.sheet.acks.d = false;
  html = settingsHtml(c.view().sheet);
  assert.ok(!/data-act="enable" data-room="d"[^>]*disabled/.test(html), 'an enabled Room D can always be turned off');
  assert.ok(/>Enable app control for this room</.test(html), 'the enable wording is visible text');

  // Names: the server message is shown; a rejected name is never sent as a success.
  hass.callWS = async msg => {
    if (msg.type === 'bose_lifestyle/set_room_name') throw { code: 'duplicate_name', message: 'Another room already has that name.' };
    return base(msg);
  };
  await c.setName('b', 'Room A');
  assert.equal(c.view().sheet.error, 'Another room already has that name.');

  // test_non_admin_cannot_call_setup_commands (card side): only administrators open the setup view.
  hass = makeHass({ admin: false, layout: fresh });
  ({ c } = make(hass)); await settle();
  await c.openSetup();
  assert.equal(c.view().sheet, null);
  assert.deepEqual(hass.calls.ws.map(m => m.type), ['bose_lifestyle/layout']);
  console.log('Setup screen: Finish setup, labels, daily sources, experimental gate, save failures and admin-only access passed');
})().catch(error => { console.error(error); process.exit(1); });


// ---- Follow-up 8b: per-room experimental opt-in and visible reasons ----
(async () => {
  const { renderMain, renderSettings: settingsHtml, renderRoom: roomHtml } = globalThis.BoseCard;
  const enabledSwitch = (html, room) => new RegExp(`data-act="experimental" data-room="${room}"(?![^>]*disabled)`).test(html);
  const lockedSwitch = (html, room) => new RegExp(`data-act="experimental" data-room="${room}"[^>]*disabled`).test(html);

  // Rooms A to C have only verified commands: no Experimental section, explanation, gate or switch. Room D keeps its own.
  let hass = makeHass({ admin: true });
  let { c } = make(hass); await settle();
  await c.openSetup();
  let html = settingsHtml(c.view().sheet);
  assert.ok(!html.includes('have not been tested on real hardware'), 'no per-room untested explanation');
  for (const room of ['a', 'b', 'c']) {
    assert.ok(!html.includes(`data-act="ack" data-room="${room}"`), `no I understand box for ${room}`);
    assert.ok(!html.includes(`data-act="experimental" data-room="${room}"`), `no experimental switch for ${room}`);
  }
  assert.ok(html.includes('data-act="ack" data-room="d"'), 'Room D keeps its I understand box');
  void enabledSwitch; void lockedSwitch;

  // test_experimental_only_buttons_show_visible_reason_until_enabled: the reason is visible text until the room is enabled.
  const withVideo2 = state => roomHtml({ id: 'b', name: 'Room B', open: true, caption: null, disabled: false,
    buttons: { ...buttons(), 'source-video-2': { entity: 'button.b_video2', disabled: state.disabled, reason: state.reason } } });
  html = withVideo2({ disabled: true, reason: 'experimental-off' });
  assert.ok(html.includes('<div class="why">Experimental; not enabled for this room</div>'), 'visible reason, not only a title');
  html = withVideo2({ disabled: false, reason: null });
  assert.ok(!html.includes('Experimental; not enabled') && html.includes('Video 2'), 'no reason once enabled');
  console.log('Experimental opt-in: only Room D has the gate; visible reasons until enabled passed');
})().catch(error => { console.error(error); process.exit(1); });


// ---- Follow-up 8c: editable bridge computer name ----
(async () => {
  const { renderMain, renderSettings: settingsHtml } = globalThis.BoseCard;
  const hass = makeHass({ admin: true, layout: { ...layoutFor(['a', 'b', 'c']), bridge_name: 'Den bridge' } });
  let { c } = make(hass); await settle();
  // test_card_shows_bridge_name_in_header_and_setup_field: header shows the nickname for everyone, setup field holds it.
  assert.ok(renderMain(c.view()).includes('Den bridge'), 'header shows the bridge nickname');
  assert.ok(/<div class="bhead"><span class="name">Den bridge<\/span><\/div>/.test(renderMain(c.view())), 'the header is the nickname as a plain title');
  await c.openSetup();
  let html = settingsHtml(c.view().sheet);
  assert.ok(html.includes('Name of this bridge computer') && html.includes('data-act="bridge-name"') && html.includes('value="Den bridge"'), 'setup field shows the nickname');
  assert.ok(html.includes('data-act="bridge-reset"'), 'a reset control sends the default');
  assert.ok(html.includes('Den bridge') && html.indexOf('Den bridge') < html.indexOf('Connected inputs'), 'the name field sits with the room names');
  await c.setBridgeName('  Den 2 ');
  assert.deepEqual(hass.calls.ws.filter(m => m.type === 'bose_lifestyle/set_bridge_name').pop(), { type: 'bose_lifestyle/set_bridge_name', name: 'Den 2' });
  assert.ok(renderMain(c.view()).includes('Den 2') && c.view().sheet.error === null, 'saved name shows in the header');
  await c.openSettings();
  assert.ok(settingsHtml(c.view().sheet).includes('Bridge computer: Den 2'), 'diagnostics show the nickname');
  // Reset sends null (the server clears the nickname) and falls back to the default.
  await c.resetBridgeName();
  assert.deepEqual(hass.calls.ws.filter(m => m.type === 'bose_lifestyle/set_bridge_name').pop(), { type: 'bose_lifestyle/set_bridge_name', name: null });
  assert.ok(renderMain(c.view()).includes('Bose Controller'), 'default after reset');
  // A refused name is shown as the server's text and never saved as success; an uncertain save is not retried.
  const base = hass.callWS;
  hass.callWS = async msg => {
    if (msg.type === 'bose_lifestyle/set_bridge_name') { hass.calls.ws.push(msg); throw { code: 'invalid_name', message: 'Enter a name from 1 to 40 characters. Control characters are not allowed.' }; }
    return base(msg);
  };
  await c.setBridgeName('   ');
  assert.equal(c.view().sheet.error, 'Enter a name from 1 to 40 characters. Control characters are not allowed.');
  hass.callWS = async msg => { if (msg.type === 'bose_lifestyle/set_bridge_name') { hass.calls.ws.push(msg); throw { code: 'uncertain', message: 'x' }; } return base(msg); };
  const before = hass.calls.ws.filter(m => m.type === 'bose_lifestyle/set_bridge_name').length;
  await c.setBridgeName('Den 3');
  assert.equal(c.view().sheet.error, "We couldn't confirm that change. Check the settings before changing them again.");
  assert.equal(hass.calls.ws.filter(m => m.type === 'bose_lifestyle/set_bridge_name').length, before + 1, 'no retry');
  assert.ok(!FORBIDDEN.some(word => (c.view().sheet.error || '').toLowerCase().includes(word)));
  // A layout without a nickname shows the default; non-admins see the header but no setup view.
  const plain = makeHass({ admin: false });
  ({ c } = make(plain)); await settle();
  assert.ok(renderMain(c.view()).includes('Bose Controller'), 'default header');
  await c.openSetup();
  assert.equal(c.view().sheet, null);
  console.log('Bridge name: header and setup field show the nickname; save, reset and failures follow the room-name rules');
})().catch(error => { console.error(error); process.exit(1); });

// ---- Task 10: accessibility, robustness and layout (DOM stub loaded in a vm context) ----
(async () => {
  const fs = require('node:fs');
  const vm = require('node:vm');
  const path = require('node:path');
  const CARD_SRC = fs.readFileSync(path.join(__dirname, '..', 'custom_components/bose_lifestyle/frontend/bose-control-card.js'), 'utf8');
  const CSS = (CARD_SRC.match(/const STYLE = `([\s\S]*?)`;/) || [])[1] || '';
  const { renderRoom: roomHtml, renderSetup: setupHtml, MESSAGES: M } = globalThis.BoseCard;
  const deferred = () => { let resolve, reject; const p = new Promise((a, b) => { resolve = a; reject = b; }); return { p, resolve, reject }; };
  const rules = [...CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(m => [m[1].trim(), m[2]]);

  // Minimal shadow root, host element and root container: records innerHTML, focus() calls and event handlers.
  function mountCard() {
    const focused = [], handlers = {}, regions = {}, shadowRef = {};
    const rootStub = () => ({
      html: '', set innerHTML(v) { this.html = v; }, get innerHTML() { return this.html; },
      querySelector(sel) {
        const m = /data-focus="([^"]+)"/.exec(sel); if (!m || !this.html.includes(`data-focus="${m[1]}"`)) return null;
        return { dataset: { focus: m[1] }, focus() { focused.push(m[1]); } };
      },
      querySelectorAll() { return []; },
    });
    const shadow = {
      template: '', activeElement: null, handlers,
      set innerHTML(v) { this.template = v; }, get innerHTML() { return this.template; },
      addEventListener(type, fn) { handlers[type] = fn; },
      getElementById(id) { if (!regions[id]) regions[id] = id === 'root' ? rootStub() : { id, textContent: '' }; return regions[id]; },
    };
    shadowRef.current = shadow;
    const defined = {};
    const ctx = {
      console, setTimeout, clearTimeout, setInterval, clearInterval, setImmediate, Promise, Date, Math, JSON, Object, Array, Set, Map, Number, String, Error, TypeError,
      customElements: { get: n => defined[n], define: (n, cls) => { defined[n] = cls; } },
      HTMLElement: class { attachShadow() { this.shadowRoot = shadowRef.current; return shadowRef.current; } },
      CustomEvent: class { constructor(t, o) { this.type = t; this.detail = o && o.detail; } },
      window: { dispatchEvent() {}, listeners: {}, addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || new Set()).add(f); },
                removeEventListener(t, f) { if (this.listeners[t]) this.listeners[t].delete(f); } },
      document: { listeners: {}, addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || new Set()).add(f); },
                  removeEventListener(t, f) { if (this.listeners[t]) this.listeners[t].delete(f); } },
    };
    vm.createContext(ctx);
    vm.runInContext(CARD_SRC, ctx);
    const card = new defined['bose-control-card']();
    return { card, shadow, focused, regions, win: ctx.window, doc: ctx.document };
  }
  const fire = (card, type, ev) => card.shadowRoot.handlers[type](ev);
  const clickAct = (act, extra = {}) => ({ target: { closest: () => ({ dataset: { act, ...extra }, disabled: false }) } });
  const clickKey = (key, entity) => ({ target: { closest: () => ({ dataset: { key, entity }, disabled: false }) } });
  const settleAll = () => new Promise(r => setImmediate(r));
  const memStore = () => { const m = new Map(); return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) }; };

  const tests = {};
  tests.test_volume_steps_setting_renders_and_saves = async () => {
    const hass = makeHass({ admin: true });
    let { c } = make(hass); await settleAll();
    await c.openSetup();
    let html = globalThis.BoseCard.renderSettings(c.view().sheet);
    assert.match(html, /<select id="volume-steps"[^>]*data-act="volume-steps"/);
    assert.ok(html.includes('<option value="1" selected>1</option>') && html.includes('<option value="5">5</option>'));
    assert.ok(!html.includes('<option value="6">'), 'five is the most');
    await c.setVolumeSteps('3');
    assert.deepEqual(hass.calls.ws.filter(m => m.type === 'bose_lifestyle/set_volume_steps'), [{ type: 'bose_lifestyle/set_volume_steps', steps: 3 }]);
    html = globalThis.BoseCard.renderSettings(c.view().sheet);
    assert.ok(html.includes('<option value="3" selected>3</option>'), 'the saved value is shown after the layout reloads');
    for (const bad of ['0', '6', 'x', '2.5', '']) await c.setVolumeSteps(bad);
    assert.equal(hass.calls.ws.filter(m => m.type === 'bose_lifestyle/set_volume_steps').length, 1, 'invalid values are never sent');
  };
  tests.test_a_volume_press_in_flight_is_not_followed_by_another = async () => {
    const hass = makeHass({ layout: { ...layoutFor(['a', 'b', 'c']), volume_steps: 3 } });
    const { c } = make(hass); await settleAll();
    let release;
    hass.callService = async (...args) => { hass.calls.services.push(args); await new Promise(r => { release = r; }); };
    const first = c.tap('button.a_volume_up', 'a', 'volume-up');
    await c.tap('button.a_volume_up', 'a', 'volume-up');   // repeat while the first is still pending: dropped
    await c.tap('button.a_volume_up', 'a', 'volume-up');
    assert.equal(hass.calls.services.length, 1, 'only the pending press was sent');
    release(); await first;
    hass.callService = async (...args) => { hass.calls.services.push(args); };
    await c.tap('button.a_volume_up', 'a', 'volume-up');
    assert.equal(hass.calls.services.length, 2, 'after it finished the next press goes out');
    await c.tap('button.a_mute', 'a', 'mute'); await c.tap('button.a_mute', 'a', 'mute');
    assert.equal(hass.calls.services.length, 4, 'other controls are unaffected');
  };
  tests.test_a_one_step_call_still_pending_when_steps_are_raised_blocks_the_next = async () => {
    const hass = makeHass({});          // starts at the default of one step
    const { c } = make(hass); await settleAll();
    const pending = [];
    hass.callService = async (...args) => { hass.calls.services.push(args); await new Promise(r => pending.push(r)); };
    const first = c.tap('button.a_volume_up', 'a', 'volume-up');
    c.layout = { ...c.layout, volume_steps: 5 };          // another client raised the setting; the layout refreshed
    await c.tap('button.a_volume_up', 'a', 'volume-up');
    assert.equal(hass.calls.services.length, 1, 'the call from the one-step period is still counted');
    pending.forEach(r => r()); await first;
    const second = c.tap('button.a_volume_up', 'a', 'volume-up');
    assert.equal(hass.calls.services.length, 2);
    pending.forEach(r => r()); await second;
  };
  tests.test_at_one_step_every_volume_tap_is_sent_even_while_one_is_pending = async () => {
    const hass = makeHass({});          // layout without volume_steps: the default of one step
    const { c } = make(hass); await settleAll();
    hass.callService = async (...args) => { hass.calls.services.push(args); await new Promise(r => setTimeout(r, 30)); };
    await Promise.all([1, 2, 3].map(() => c.tap('button.a_volume_up', 'a', 'volume-up')));
    assert.equal(hass.calls.services.length, 3, 'three quick taps are three sends at the default');
  };
  const volumeDown = { button: 0, target: { closest: () => ({ dataset: { key: 'volume-up', entity: 'button.a_volume_up', room: 'a' }, disabled: false }) } };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  async function holdScenario(end) {
    const { card, win, doc } = mountCard();
    card.hass = makeHass(); await settleAll();
    card.isConnected = true; card.connectedCallback();
    let taps = 0; card.controller.tap = () => { taps += 1; };
    fire(card, 'pointerdown', volumeDown);
    await sleep(1400);
    assert.ok(taps >= 3, 'a held volume key repeats while the pointer is down');
    end(card, win, doc);
    const after = taps;
    await sleep(800);
    assert.equal(taps, after, 'no volume press is sent after the hold ended');
    assert.equal(card.holdRepeat, null, 'the repeat timer is cleared');
    card.disconnectedCallback();
    return { win, doc };
  }
  tests.test_hold_stops_when_card_is_removed = () => holdScenario(card => { card.isConnected = false; card.disconnectedCallback(); });
  tests.test_hold_stops_when_pointer_released_outside_card = () => holdScenario((card, win) => { for (const f of win.listeners.pointerup) f(); });
  tests.test_other_pointer_release_does_not_end_a_hold = async () => {
    const { card, win } = mountCard();
    card.hass = makeHass(); await settleAll();
    card.isConnected = true; card.connectedCallback();
    let taps = 0; card.controller.tap = () => { taps += 1; };
    fire(card, 'pointerdown', { ...volumeDown, pointerId: 1 });
    await sleep(900);
    for (const f of win.listeners.pointerup) f({ pointerId: 2 });   // a second finger lifts elsewhere
    const before = taps;
    await sleep(700);
    assert.ok(taps > before, 'the hold continues when another pointer is released');
    for (const f of win.listeners.pointerup) f({ pointerId: 1 });
    const after = taps;
    await sleep(700);
    assert.equal(taps, after, 'releasing the holding pointer ends it');
    card.disconnectedCallback();
  };
  tests.test_hold_stops_when_window_loses_focus_or_hides = async () => {
    await holdScenario((card, win) => { for (const f of win.listeners.blur) f(); });
    await holdScenario((card, win, doc) => { for (const f of doc.listeners.visibilitychange) f(); });
  };
  tests.test_card_removal_unregisters_window_listeners = async () => {
    const { win, doc } = await holdScenario(card => { card.isConnected = false; card.disconnectedCallback(); });
    assert.ok([...(win.listeners.pointerup || [])].length === 0 && [...(doc.listeners.visibilitychange || [])].length === 0, 'listeners removed on disconnect');
  };
  tests.test_error_region_is_aria_live = async () => {
    const { card, shadow } = mountCard();
    card.hass = makeHass({ failPress: true }); await settleAll();
    assert.match(shadow.template, /id="alert"[^>]*role="alert"[^>]*aria-live="assertive"/, 'one assertive error region');
    assert.equal((shadow.template.match(/role="alert"/g) || []).length, 1);
    await fire(card, 'click', clickKey('mute', 'button.a_mute'));
    assert.equal(shadow.getElementById('alert').textContent, M.uncertain, 'an uncertain press is announced');
    assert.equal((shadow.getElementById('root').html.match(/role="alert"/g) || []).length, 0, 'no second alert region is rendered');
    card.controller.sheet = null;
    const admin = makeHass({ admin: true }); card.hass = admin; await settleAll();
    await fire(card, 'click', clickAct('gear')); await settleAll();
    card.controller.sheet.error = 'Couldn\'t load settings.';
    card.render(undefined);
    assert.equal(shadow.getElementById('alert').textContent, 'Couldn\'t load settings.', 'settings errors are announced');
  };

  tests.test_status_region_is_polite_live = async () => {
    const { card, shadow } = mountCard();
    assert.match(shadow.template, /id="status"[^>]*role="status"[^>]*aria-live="polite"/, 'one polite status region');
    card.hass = makeHass({ health: 'unavailable' }); await settleAll();
    assert.equal(shadow.getElementById('status').textContent, 'Sound controls are unavailable. Use the Bose remote.');
    assert.equal(shadow.getElementById('alert').textContent, '', 'nothing urgent to announce');
    const fault = makeHass({ health: 'serial-open' }); fault.states['sensor.health'].attributes.fault = 'latched';
    card.hass = fault; await settleAll();
    assert.equal(shadow.getElementById('alert').textContent, M.fault_paused, 'a fault is announced assertively');
  };

  tests.test_buttons_have_accessible_names_with_room_and_function = async () => {
    const buttons = Object.fromEntries(KEYS.map(k => [k, { entity: `button.a_${k}`, disabled: false }]));
    const html = roomHtml({ id: 'a', name: 'Room A', open: true, caption: null, disabled: false, buttons });
    assert.ok(html.includes('aria-label="Room A, Volume up"') && html.includes('aria-label="Room A, Mute"'));
    for (const [, label] of html.matchAll(/<button class="ctl[^"]*"[^>]*aria-label="([^"]+)"/g)) assert.ok(label.startsWith('Room A, '), label);
    const flagged = roomHtml({ id: 'a', name: 'Room A', open: true, caption: null, disabled: false, buttons: { mute: { entity: 'button.a_mute', disabled: true, flag: 'Not sent' } } });
    assert.ok(flagged.includes('aria-label="Room A, Mute, Not sent"'), 'a not-sent control says so to a screen reader');
  };

  tests.test_switches_have_role_or_native_checkbox_with_label = async () => {
    const view = { rooms: [{ id: 'a', name: 'Room A', enabled: true, commissioning: 'Commissioned', experimental: false },
      { id: 'd', name: 'Room D', enabled: false, commissioning: 'No zone installed, commands inferred', experimental: false }],
      sources: [{ source: 'am', label: 'AM radio', connected: true }], acks: {}, mapping: '', bridgeName: 'Bose Controller' };
    const html = setupHtml(view);
    const inputs = html.match(/<input type="checkbox"[^>]*>/g) || [];
    assert.ok(inputs.length >= 4, 'the setup view has checkboxes');   // Room A switch, AM source, Room D gate and switch
    for (const input of inputs) {
      const idx = html.indexOf(input);
      const insideLabel = html.lastIndexOf('<label', idx) > html.lastIndexOf('</label>', idx);
      assert.ok(/aria-label="[^"]+"/.test(input) || insideLabel, `unlabeled switch: ${input}`);
    }
    assert.ok(/<label class="gate"><input type="checkbox" class="cbox" data-act="ack" data-room="d"[^>]*>/.test(html) && html.includes('I understand these commands are untested'), 'the Experimental gate is one label-associated box');
  assert.ok(html.includes('Tick the box above to unlock this switch.') && !/Commissioned|No zone installed/.test(html), 'plain wording only: no commissioning jargon in setup');
  };

  tests.test_focus_kept_after_update = async () => {
    const { card, shadow, focused } = mountCard();
    card.hass = makeHass({ admin: false }); await settleAll();
    // A real change (a fault appears) rebuilds the room list; focus must return to the same control.
    const hass = makeHass({ admin: false });
    shadow.activeElement = { dataset: { focus: 'toggle:b' }, classList: { contains: () => false } };
    hass.states['sensor.health'] = { state: 'serial-open', attributes: { fault: 'latched' } };
    focused.length = 0; card.hass = hass; await settleAll();
    assert.deepEqual(focused, ['toggle:b'], 'focus returns to the same control by its data-focus key');
    shadow.activeElement = { dataset: { focus: 'ctl:button.zz_gone' }, classList: { contains: () => false } };
    hass.states['sensor.health'] = { state: 'serial-open', attributes: { fault: 'other' } };
    focused.length = 0; card.hass = hass; await settleAll();
    assert.deepEqual(focused, [], 'a key that is no longer rendered is not focused, and nothing throws');
  };

  tests.test_settings_sheet_esc_closes_and_restores_focus = async () => {
    const { card, shadow, focused } = mountCard();
    card.hass = makeHass({ admin: true }); await settleAll();
    await fire(card, 'click', clickAct('gear')); await settleAll();
    assert.ok(card.controller.sheet, 'the sheet is open');
    assert.equal(focused[focused.length - 1], 'close', 'focus moves into the sheet');
    await fire(card, 'keydown', { key: 'Escape', preventDefault() {} });
    assert.equal(card.controller.sheet, null, 'Esc closes the sheet');
    assert.equal(focused[focused.length - 1], 'gear', 'focus returns to the settings opener');
  };

  tests.test_pending_settings_request_dismissed_without_throw = async () => {
    for (const outcome of ['resolve', 'reject']) {
      for (const call of ['source', 'room']) {
        const hass = makeHass({ admin: true });
        const c = new Controller({ storage: memStore(), haptic() {}, now: () => 1000000, onChange: () => {} });
        c.setHass(hass); await settleAll(); await c.openSetup();
        const d = deferred(); const base = hass.callWS;
        const target = call === 'source' ? 'bose_lifestyle/set_source' : 'bose_lifestyle/set_room';
        hass.callWS = msg => (msg.type === target ? d.p : base(msg));
        const pending = call === 'source' ? c.setSource('tape', true) : c.apply({ room: 'b', enabled: false });
        c.closeSettings();
        if (outcome === 'resolve') d.resolve({ rooms: [], setup: { sources: [] } }); else d.reject({ code: 'uncertain', message: 'x' });
        const layoutsBefore = hass.calls.ws.filter(m => m.type === 'bose_lifestyle/layout').length;
        await pending;   // must not throw (TypeError on a closed sheet)
        await settleAll();
        if (outcome === 'resolve') assert.ok(hass.calls.ws.filter(m => m.type === 'bose_lifestyle/layout').length > layoutsBefore, `${call}: the main card reloads after a dismissed save`);
        assert.equal(c.view().sheet, null, `${call} ${outcome}: the sheet stays closed`);
      }
    }
  };

  tests.test_double_submit_ignored_while_pending = async () => {
    const hass = makeHass({ admin: true });
    const c = new Controller({ storage: memStore(), haptic() {}, now: () => 1000000, onChange: () => {} });
    c.setHass(hass); await settleAll(); await c.openSetup();
    const d = deferred(); const base = hass.callWS; let sent = 0;
    hass.callWS = msg => { if (msg.type === 'bose_lifestyle/set_source') { sent++; return d.p; } return base(msg); };
    const a = c.setSource('tape', true), b = c.setSource('tape', true);
    d.resolve({ rooms: [], setup: { sources: [] } });
    await Promise.all([a, b]);
    assert.equal(sent, 1, 'the second submit is ignored while the first is pending');
  };

  tests.test_setup_dropdowns_keep_normal_tab_order = async () => {
    const { card, shadow } = mountCard();
    card.controller.sheet = {};
    const nodes = ['button', 'select', 'input', 'select'].map(tag => ({
      tag, disabled: false, focus() { shadow.activeElement = this; },
    }));
    // Model the browser selector: omitted element types must not silently appear in its results.
    card.rootEl.querySelectorAll = selector => nodes.filter(node =>
      selector.split(',').some(part => part.trim() === `${node.tag}:not([disabled])`) && !node.disabled);
    const tab = shiftKey => {
      let prevented = false;
      fire(card, 'keydown', { key: 'Tab', shiftKey, preventDefault() { prevented = true; } });
      return prevented;
    };
    for (const shiftKey of [false, true]) {
      shadow.activeElement = nodes[1];
      assert.equal(tab(shiftKey), false, 'Tab from an interior dropdown must follow browser focus order');
      assert.equal(shadow.activeElement, nodes[1], 'the handler must not move an interior dropdown focus');
    }
    shadow.activeElement = nodes[3];
    assert.equal(tab(false), true);
    assert.equal(shadow.activeElement, nodes[0], 'the final dropdown wraps to the first control');
    assert.equal(tab(true), true);
    assert.equal(shadow.activeElement, nodes[3], 'backward wrap includes the final dropdown');
    nodes[3].disabled = true;
    shadow.activeElement = nodes[0];
    assert.equal(tab(true), true);
    assert.equal(shadow.activeElement, nodes[2], 'disabled dropdowns are excluded');
  };

  tests.test_controls_have_min_target_size_in_css = async () => {
    const TARGET = /\.(ctl|pill|gear|title|rname|sw|links a)\b|input\[type=checkbox\]|\bsummary\b/;
    let checked = 0;
    for (const [sel, body] of rules) {
      if (!TARGET.test(sel) || /span|::after|:checked|:focus|input\[type=checkbox\]/.test(sel)) continue;   // checkboxes are checked below: the row is the target
      for (const m of body.matchAll(/(?<![-\w])(min-)?(height|width)\s*:\s*(\d+(?:\.\d+)?)px/g)) {
        checked++;
        assert.ok(Number(m[3]) >= 44, `${sel} sets ${m[0]}, below 44 px`);
      }
    }
    assert.ok(checked >= 8, 'the target rules were found');
    for (const [sel, body] of rules) {
      if (/\.(ctl|pill|gear|rname|sw)\b/.test(sel) && !/ span/.test(sel)) assert.ok(!/min-width:\s*0\b/.test(body), `${sel} must keep its 44 px minimum width`);
    }
    for (const [sel, body] of rules) {
      if (!sel.includes('input[type=checkbox]')) continue;
      for (const m of body.matchAll(/(?<![-\w])(min-)?(height|width)\s*:\s*(\d+(?:\.\d+)?)px/g)) assert.ok(Number(m[3]) < 40, `checkbox ${m[0]} must stay a normal size`);
    }
    assert.match(CSS, /\.rrow\.ack \{[^}]*display:\s*flex[^}]*min-height:\s*44px/, 'the acknowledgement row is a compact flex row with a 44 px target');
    assert.match(CSS, /summary \{[^}]*min-height:\s*44px/, 'the Technical details summary has a 44 px target');
    assert.match(CSS, /\.ctl \{[^}]*min-height:\s*44px[^}]*min-width:\s*44px/);
    assert.match(CSS, /\.seg \.ctl \{[^}]*min-height:\s*44px/);
    assert.match(CSS, /\.pill \{[^}]*min-height:\s*44px/);
  };

  tests.test_focus_visible_rule_present = async () => {
    const rule = rules.find(([sel]) => sel.includes(':focus-visible') && !sel.includes('.rname'));
    assert.ok(rule && /outline:\s*3px solid/.test(rule[1]), 'a :focus-visible outline rule exists');
    assert.ok(/\.sw input:focus-visible \+ span/.test(CSS), 'the hidden switch input shows its ring on the visible track');
    assert.ok(!/outline:\s*0/.test(CSS), 'no rule removes the outline unconditionally');
    assert.ok(!CSS.includes(':hover'), 'no hover-only affordance');
  };

  tests.test_no_fixed_widths_over_440_in_css = async () => {
    for (const m of CSS.matchAll(/(?<![-\w])width\s*:\s*(\d+(?:\.\d+)?)px/g)) assert.ok(Number(m[1]) <= 440, `width ${m[0]} is wider than 440 px`);
    assert.match(CSS, /:host \{[^}]*max-width:\s*480px[^}]*margin:\s*0 auto/, 'the card is a centered max-width column');
    assert.match(CSS, /\.rrow > \* \{[^}]*min-width:\s*0/, 'grid children may shrink');
    assert.ok(!/font-size:\s*\d+px/.test(CSS.replace(/:host \{[^}]*\}/, '')), 'text sizes are em of the card base, so 200% text scales');
  };

  tests.test_safe_area_insets_used = async () => {
    assert.ok(CSS.includes('env(safe-area-inset-left)') && CSS.includes('env(safe-area-inset-right)'));
  };

  tests.test_long_name_class_wraps = async () => {
    assert.match(CSS, /\.name \{[^}]*overflow-wrap:\s*anywhere[^}]*min-width:\s*0|\.name \{[^}]*min-width:\s*0[^}]*overflow-wrap:\s*anywhere/, 'names wrap inside their box');
    const long = 'W'.repeat(40);
    const html = roomHtml({ id: 'a', name: long, open: true, caption: null, disabled: false, buttons: {} });
    assert.ok(html.includes(`<span class="name">${long}</span>`), 'a 40-character name renders whole');
  };

  tests.test_no_rem_units_and_host_font_size_anchor = async () => {
    assert.ok(!/\drem\b/.test(CSS), 'no rem units: rem follows the host page, not the card');
    assert.match(CSS, /:host \{[^}]*font-size:\s*16px/, 'the card sets its own 16px base');
    assert.match(CSS, /:host \{[^}]*-webkit-text-size-adjust:\s*100%/, 'no iOS text-size inflation');
    assert.ok(!/font-size:\s*larger|font-size:\s*[\d.]+v[wh]|clamp\(|\bzoom\s*:/.test(CSS), 'no viewport-relative, larger or zoom text sizing');
    assert.match(CSS, /\.seg\.src \{[^}]*grid-template-columns:\s*repeat\(auto-fit/, 'source buttons wrap in a grid');
    assert.match(CSS, /\.seg\.src \.ctl span \{[^}]*text-overflow:\s*ellipsis/, 'a long source label ellipsises on one line');
  };

  tests.test_reduced_motion_media_query_present = async () => {
    assert.match(CSS, /@media \(prefers-reduced-motion: reduce\) \{[\s\S]*transition:\s*none/);
  };

  let passed = 0;
  for (const [name, fn] of Object.entries(tests)) {
    try { await fn(); passed++; } catch (error) { console.error(`FAIL ${name}`); console.error(error); process.exit(1); }
  }
  console.log(`Task 10 accessibility and layout: ${passed} tests passed`);
})().catch(error => { console.error(error); process.exit(1); });

// test_no_british_spellings_in_user_facing_text: American spelling in strings.json and the card (fragments keep this file clean).
{
  const fs = require('node:fs');
  const path = require('node:path');
  const BRITISH = new RegExp(['label' + 'led', 'col' + 'our', 'behav' + 'iour', 'cent' + 'red', 'favour' + 'ite', 'g' + 'rey'].join('|'), 'i');
  for (const file of ['../custom_components/bose_lifestyle/strings.json', '../custom_components/bose_lifestyle/frontend/bose-control-card.js']) {
    const text = fs.readFileSync(path.join(__dirname, file), 'utf8');
    assert.ok(!BRITISH.test(text), `${file} uses a British spelling: ${(text.match(BRITISH) || [''])[0]}`);
  }
}

// ---- Task 10d: separate source tiles, nicknames, Last sent, icons and the setup picker ----
(async () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const CARD_SRC = fs.readFileSync(path.join(__dirname, '..', 'custom_components/bose_lifestyle/frontend/bose-control-card.js'), 'utf8');
  const CSS = (CARD_SRC.match(/const STYLE = `([\s\S]*?)`;/) || [])[1] || '';
  const rules = [...CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(m => [m[1].trim(), m[2]]);
  const ruleOf = sel => { const hit = rules.filter(([s]) => s.split('*/').pop().trim() === sel); assert.ok(hit.length, `rule ${sel}`); return hit[hit.length - 1][1]; };   // a CSS comment may sit before the selector
  const { renderRoom: roomHtml, renderSetup: setupHtml, INPUT_ICONS, Controller: C } = globalThis.BoseCard;
  const TILES = html => html.match(/<button class="ctl tile"[^>]*>/g) || [];
  const settleAll = () => new Promise(r => setImmediate(r));
  const src = (label, icon) => ({ entity: `button.a_${label}`, disabled: false, label, icon });
  const room = (buttons, extra = {}) => roomHtml({ id: 'a', name: 'Room A', open: true, caption: null, disabled: false, buttons, ...extra });

  // test_source_buttons_are_separate_tiles_with_gap_and_own_background
  // Tiles: each source is its own button with its own background and border; the container is not a shared pill.
  assert.ok(!/background:(?!\s*none)/.test(ruleOf('.seg.src')), 'the container has no shared background');
  assert.match(ruleOf('.seg.src'), /gap:\s*8px/);
  const tile = ruleOf('.seg.src .ctl');
  assert.match(tile, /background:/); assert.match(tile, /border:\s*1px solid/); assert.match(tile, /border-radius:\s*12px/); assert.match(tile, /min-height:\s*56px/);
  let html = room({ tv: { entity: 'button.a_tv', disabled: false }, music: { entity: 'button.a_music', disabled: false }, 'source-am': { entity: 'button.a_am', disabled: false } });
  assert.ok(html.includes('<div class="seg src">'));
  assert.equal(TILES(html).length, 3, 'one tile per source');
  console.log('ok source tiles are separate');

  // test_source_grid_two_columns_at_393_and_wraps_at_320
  // Grid: 2 columns at 393 and 320, 3 at 440 and wider (the 480 px card cap keeps 4 out of reach). Arithmetic from the CSS: 8em tiles, 8 px gaps, 16 px side padding.
  assert.match(ruleOf('.seg.src'), /grid-template-columns:\s*repeat\(auto-fit,\s*minmax\(8em,\s*1fr\)\)/);
  const columns = viewport => { const inner = Math.min(viewport, 480) - 32; return Math.max(1, Math.floor((inner + 8) / (128 + 8))); };
  assert.equal(columns(393), 2); assert.equal(columns(320), 2); assert.equal(columns(440), 3); assert.equal(columns(768), 3); assert.equal(columns(1440), 3);
  assert.match(ruleOf('.seg.src .ctl span'), /-webkit-line-clamp:\s*2/, 'a label wraps to two lines at most');
  assert.match(ruleOf('.seg.src .ctl span'), /overflow-wrap:\s*anywhere/, 'a long nickname breaks inside the tile');
  // test_source_grid_has_no_pill_background: the multi-row tile grid is not drawn as the one-row pill (no oval behind the tiles).
  assert.doesNotMatch(ruleOf('.seg.src'), /999px/, 'no pill radius on the grid');
  assert.match(ruleOf('.seg.src'), /background:\s*none/, 'no pill background on the grid');
  console.log('ok grid and label clamp');

  // test_nickname_shown_in_selector_and_accessible_name
  // Nickname and icon are used for the tile text and the accessible name, escaped.
  html = room({ 'source-cd': src('Den <CD>', 'disc') });
  assert.ok(html.includes('aria-label="Room A, Den &lt;CD&gt;"'), 'accessible name carries the nickname, escaped');
  assert.ok(html.includes('Den &lt;CD&gt;') && !html.includes('<CD>'));
  assert.ok(html.includes(INPUT_ICONS.disc.path), 'the chosen icon is drawn');

  // test_unknown_stored_icon_falls_back
  // An unknown or stale icon id falls back to the default icon for that source (am is radio).
  html = room({ 'source-am': src('Den', 'spaceship') });
  assert.ok(html.includes(INPUT_ICONS.radio.path) && !html.includes('spaceship'), 'unknown icon falls back to the default');
  html = room({ tv: { entity: 'button.a_tv', disabled: false } });   // no inputs at all: the familiar key label
  assert.ok(html.includes('aria-label="Room A, TV"'));

  // test_last_sent_marker_text
  // Last sent: text on the tile, and only for the room's most recent source press.
  html = room({ 'source-am': src('AM radio', 'radio'), 'source-cd': src('CD player', 'disc') }, { lastSent: 'source-am' });
  assert.equal(html.split('<em class="tag">Last sent</em>').length - 1, 1, 'one marker, as text');
  assert.ok(html.includes('aria-label="Room A, AM radio, Last sent"'));
  assert.ok(!/>[^<]*current/i.test(html), 'never a visible "current" label (currentColor in the icon is an attribute)');
  assert.ok(!room({ 'source-am': src('AM radio', 'radio') }).includes('Last sent'), 'no marker without a press');
  console.log('ok nickname, icon fallback and last sent');

  // Controller: the marker follows the room's most recent source press; volume presses do not move it.
  const hass = makeHass({ admin: false });
  let clockNow = 1000000;
  const c = new C({ storage: { getItem: () => null, setItem() {} }, haptic() {}, now: () => clockNow, onChange: () => {} });
  c.setHass(hass); await settleAll();
  await c.tap('button.a_source_cd', 'a', 'source-cd');
  assert.equal(c.view().rooms[0].lastSent, 'source-cd');
  await c.tap('button.a_volume_up', 'a', 'volume-up');
  assert.equal(c.view().rooms[0].lastSent, 'source-cd', 'CD then volume up keeps CD marked');
  assert.equal(c.view().rooms[1].lastSent, null, 'another room is not marked');
  clockNow += 3000;                                        // inputs wait out the cooldown between presses
  await c.tap('button.b_source_am', 'b', 'source-am');
  assert.equal(c.view().rooms[1].lastSent, 'source-am');
  const pressOk = hass.callService; hass.callService = async () => { throw new Error('press failed'); };
  clockNow += 3000;
  await c.tap('button.a_source_fm', 'a', 'source-fm');   // a press that fails is never marked
  hass.callService = pressOk;
  assert.equal(c.view().rooms[0].lastSent, 'source-cd', 'an unsent press does not move the marker');
  console.log('ok controller last sent');

  // test_card_gate_stays_ticked_once_room_d_is_on: Room D enabled shows the gate ticked and On, even with no acks.
  {
    const on = setupHtml({ rooms: [{ id: 'd', name: 'Room D', enabled: true, commissioning: 'x', experimental: true }], sources: [], mapping: '', acks: {}, bridgeName: 'x', inputs: {} });
    assert.match(on, /<input type="checkbox" class="cbox" data-act="ack" data-room="d"[^>]* checked>/, 'gate ticked');
    assert.ok(on.includes('<span class="gate-state">On</span>'), 'shows On');
  }
  // test_setup_view_has_name_field_and_icon_picker_per_source
  // Setup view: nickname field with Use default, and an icon radio group per source, outside the connected switch.
  const sources = [{ source: 'am', label: 'AM radio', connected: true }, { source: 'cd', label: 'CD player', connected: false }];
  const inputs = { am: { name: 'Den AM', icon: 'radio' }, cd: { name: 'CD player', icon: 'disc' } };
  html = setupHtml({ rooms: [], sources, mapping: '', acks: {}, bridgeName: 'Bose Controller', inputs });
  assert.ok(html.includes('data-act="input-name" data-source="am"') && html.includes('value="Den AM"'), 'nickname field with the current name');
  assert.ok(html.includes('maxlength="30"'), 'the nickname field allows 30 characters');
  assert.ok(html.includes('data-act="input-default" data-source="am"') && html.includes('Use default'));
  assert.equal((html.match(/<input type="radio"[^>]*>/g) || []).length, 22, 'eleven icons per source');
  assert.equal((html.match(/<fieldset class="icons">/g) || []).length, 2);
  assert.ok(html.includes('<legend class="meta">Icon for AM radio</legend>'));
  assert.equal((html.match(/name="icon-am"[^>]*checked/g) || []).length, 1, 'the current icon is selected');
  const switchAt = html.indexOf('data-act="source" data-source="am"');
  assert.ok(html.indexOf('</label>', switchAt) < html.indexOf('data-act="input-name" data-source="am"'), 'the nickname sits outside the connected switch');
  assert.ok(html.indexOf('</label>', switchAt) < html.indexOf('name="icon-am"'), 'the icon group sits outside the connected switch');
  // test_icon_picker_is_labeled_radio_group_44px_targets
  assert.match(ruleOf('.ico'), /min-height:\s*44px/); assert.match(ruleOf('.ico'), /min-width:\s*5em/);
  assert.match(ruleOf('.ico-box'), /width:\s*44px/); assert.match(ruleOf('.ico-box'), /height:\s*44px/);
  assert.match(CSS, /\.ico input:checked ~ \.ico-box \{[^}]*border:\s*2px solid/, 'the selected icon is visible');
  assert.match(CSS, /\.ico input:focus-visible ~ \.ico-box \{[^}]*outline:\s*3px solid/, 'the icon focus ring is visible');
  const ids = Object.keys(INPUT_ICONS);
  assert.deepEqual(ids.slice().sort(), ['computer', 'disc', 'game', 'input', 'music', 'phone', 'radio', 'speaker', 'streaming', 'tape', 'tv'], 'the eleven built-in icons');
  for (const id of ids) assert.ok(INPUT_ICONS[id].label && INPUT_ICONS[id].path.includes('<'), id);
  console.log('ok setup view nickname and icon picker');

  // Saves: the change sends only the field that changed; an empty name is refused with the server's text; an uncertain save is not retried.
  const hassN = makeHass({ admin: true });
  const base = hassN.callWS; const seen = [];
  hassN.callWS = async msg => {
    if (msg.type !== 'bose_lifestyle/set_input') return base(msg);
    seen.push(msg);
    if (msg.name === '') throw { code: 'invalid_name', message: 'Enter a nickname from 1 to 30 characters. Control characters are not allowed.' };
    if (msg.source === 'tape') throw { code: 'uncertain', message: 'x' };
    return { rooms: [], setup: { sources: [] }, inputs: {} };
  };
  const cn = new C({ storage: { getItem: () => null, setItem() {} }, haptic() {}, now: () => 1000000, onChange: () => {} });
  cn.setHass(hassN); await settleAll(); await cn.openSetup();
  await cn.setInput('cd', { name: 'Den CD' });
  assert.deepEqual(seen[0], { type: 'bose_lifestyle/set_input', source: 'cd', name: 'Den CD' }, 'name only: icon not sent');
  await cn.setInput('cd', { icon: 'tv' });
  assert.deepEqual(seen[1], { type: 'bose_lifestyle/set_input', source: 'cd', icon: 'tv' }, 'icon only: name not sent');
  await cn.setInput('cd', { name: null });
  assert.deepEqual(seen[2], { type: 'bose_lifestyle/set_input', source: 'cd', name: null }, 'Use default sends null only');
  await cn.setInput('cd', { name: '' });
  assert.equal(cn.view().sheet.error, 'Enter a nickname from 1 to 30 characters. Control characters are not allowed.');
  await cn.setInput('tape', { icon: 'tape' });
  assert.equal(cn.view().sheet.error, "We couldn't confirm that change. Check the settings before changing them again.", 'an uncertain save uses the standard setup text');
  assert.equal(seen.filter(m => m.source === 'tape').length, 1, 'no retry');
  console.log('Card Task 10d: separate tiles, nicknames, last sent, icon fallback and the setup picker passed');
})().catch(error => { console.error(error); process.exit(1); });

// ---- Fix 10e: caption uses the input nickname; setup save failures share one mapping; no M1 jargon in user text ----
(async () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const { renderSettings: settingsHtml, CONFIRM_D: CONFIRM, MESSAGES: M } = globalThis.BoseCard;
  const settleAll = () => new Promise(r => setImmediate(r));
  const TOKEN = ['M', '1'].join('');
  const JARGON = new RegExp(`\\b${TOKEN}\\b`);

  // test_last_sent_caption_uses_nickname: the caption names the input the way its tile does; no nickname keeps the fixed label.
  const nowSec = 1000;
  const shot = action => ({ action, outcome: 'transmitted-with-matching-response', observed_at: nowSec });
  const inputs = { 'video-1': { name: 'Den TV', icon: 'tv' }, aux: { name: 'Den Music', icon: 'music' } };
  assert.equal(lastSent(shot('room-c-source-video-1'), 'c', nowSec, 'Room C', inputs), 'Last sent: Den TV · just now');
  assert.equal(lastSent(shot('room-c-source-aux'), 'c', nowSec, 'Room C', inputs), 'Last sent: Den Music · just now');
  assert.equal(lastSent(shot('room-c-source-video-1'), 'c', nowSec), 'Last sent: TV · just now', 'no inputs: the fixed label');
  assert.equal(lastSent(shot('room-c-volume-up'), 'c', nowSec, 'Room C', inputs), 'Last sent: Volume up · just now', 'non-source actions unchanged');
  const hassCap = makeHass({ admin: false, layout: { ...layoutFor(['a', 'b', 'c']), inputs: { 'video-1': { name: 'Den TV', icon: 'tv' } } },
    outcome: { action: 'room-c-source-video-1', outcome: 'transmitted-with-matching-response', observed_at: Date.now() / 1000 } });
  const cCap = new Controller({ storage: { getItem: () => null, setItem() {} }, haptic() {}, now: () => Date.now(), onChange: () => {} });
  cCap.setHass(hassCap); await settleAll();
  assert.match(cCap.view().rooms.find(r => r.id === 'c').caption, /^Last sent: Den TV · /, 'the room caption uses the nickname');

  // test_input_and_room_name_save_failures_use_shared_mapping: setup saves use the same outcome text; no retry control is offered.
  const cases = [
    [{ code: 'not_saved', message: 'x' }, 'Nothing was changed. The settings could not be saved.'],
    [{ code: 'dry_run', message: 'x' }, "That change wasn't saved. Nothing was changed."],
    [{ code: 'not_ready', message: 'x' }, "That change wasn't saved. Nothing was changed."],
    [{ code: 'invalid_request', message: 'Invalid input' }, "That change wasn't saved. Nothing was changed."],
    [{ code: 'uncertain', message: 'x' }, "We couldn't confirm that change. Check the settings before changing them again."],
    [{ code: 'bridge_error', message: 'x' }, "We couldn't confirm that change. Check the settings before changing them again."],
  ];
  for (const [error, text] of cases) {
    for (const type of ['bose_lifestyle/set_input', 'bose_lifestyle/set_room_name']) {
      const hass = makeHass({ admin: true });
      const base = hass.callWS;
      hass.callWS = async msg => { if (msg.type === type) throw error; return base(msg); };
      const cs = new Controller({ storage: { getItem: () => null, setItem() {} }, haptic() {}, now: () => 1000000, onChange: () => {} });
      cs.setHass(hass); await settleAll(); await cs.openSetup();
      if (type.endsWith('set_input')) await cs.setInput('cd', { icon: 'tv' }); else await cs.setName('b', 'Garden');
      assert.equal(cs.view().sheet.error, text, `${type} ${error.code}`);
      const html = settingsHtml(cs.view().sheet);
      assert.ok(html.includes(text.replace(/'/g, '&#39;')), 'the text is shown, escaped');
      assert.ok(!/data-act="retry"|>\s*Retry|try again/i.test(html), 'no retry control');
    }
  }
  // Server validation text is still shown for a name the server refused.
  {
    const hass = makeHass({ admin: true });
    const base = hass.callWS;
    hass.callWS = async msg => { if (msg.type === 'bose_lifestyle/set_input') throw { code: 'invalid_name', message: 'Enter a nickname from 1 to 30 characters. Control characters are not allowed.' }; return base(msg); };
    const cv = new Controller({ storage: { getItem: () => null, setItem() {} }, haptic() {}, now: () => 1000000, onChange: () => {} });
    cv.setHass(hass); await settleAll(); await cv.openSetup();
    await cv.setInput('cd', { name: '' });
    assert.equal(cv.view().sheet.error, 'Enter a nickname from 1 to 30 characters. Control characters are not allowed.');
  }

  // test_no_m1_jargon_in_user_text: the card's user-visible text says the Bose control unit, never the model shorthand.
  const cardSource = fs.readFileSync(path.join(__dirname, '..', 'custom_components/bose_lifestyle/frontend/bose-control-card.js'), 'utf8');
  const visible = cardSource.split('\n').filter(line => !line.trimStart().startsWith('//'));
  assert.ok(!visible.some(line => JARGON.test(line.replace(/<[^>]*>/g, ''))), 'no model shorthand in card text');
  assert.ok(!JARGON.test(CONFIRM), 'the Room D confirmation');
  assert.ok(CONFIRM.includes('the Bose control unit'), 'the confirmation names the unit');
  const expHtml = settingsHtml({ rooms: [{ id: 'd', name: 'Room D', enabled: false, commissioning: 'x', experimental: false }], sources: [], mapping: '', acks: {}, bridgeName: 'x', inputs: {}, error: null, confirm: null, events: [] });
  assert.ok(!JARGON.test(expHtml), 'the Experimental text');
  console.log('Card fix 10e: caption nickname, shared save failure mapping and no model shorthand passed');
})().catch(error => { console.error(error); process.exit(1); });

// ---- Task 10g: IR target and receiver label, config-uncertain layout, stale alerts, dismiss and Back wording ----
(async () => {
  const { renderSetup, renderMain, irLine } = globalThis.BoseCard;
  const settle = () => new Promise(r => setImmediate(r));
  const quietController = hass => { const c = new Controller({ storage: { getItem: () => null, setItem() {} }, haptic() {}, now: () => 1000000, onChange() {} }); c.setHass(hass); return c; };
  const ROOMS = [['a', 'Lounge'], ['b', 'Garden'], ['c', 'Room C'], ['d', 'Room D']].map(([id, name]) => ({ id, name, enabled: id !== 'd', live: id !== 'd', commissioning: 'Commissioned', experimental: false }));
  const SETUP = { rooms: ROOMS, sources: SETUP_STUB.sources, mapping: SETUP_STUB.mapping, bridgeName: 'Den', inputs: {}, acks: {}, error: null, confirm: null, events: [] };
  const NAMES4 = { a: 'Lounge', b: 'Garden', c: 'Room C', d: 'Room D' };
  const withIr = zone => ({ ...layoutFor(['a', 'b', 'c'], NAMES4), ir_target: { zone, zone_name: NAMES4[zone], allowed: true, reason: null }, ir_receiver_label: null });

  // test_setup_has_ir_target_selector_and_receiver_label: the label field, the room selector by nickname, and the current line.
  {
    const html = renderSetup({ ...SETUP, irTarget: { zone: 'b', zone_name: 'Garden', allowed: true, reason: null }, irLabel: 'Kitchen', backTo: 'settings' });
    assert.ok(html.includes('Where is the IR receiver?') && html.includes('value="Kitchen"') && html.includes('maxlength="40"'));
    assert.ok(html.includes('Which room does it control?') && /<select[^>]*data-act="ir-target"/.test(html));
    assert.ok(html.includes('<option value="a">Lounge</option>') && html.includes('<option value="b" selected>Garden</option>'));
    assert.ok(html.includes('This remote controls Garden'), 'the existing target line stays');
    assert.ok(!html.includes('mute state will be forgotten'), 'no notice until a change is made');
    assert.ok(html.includes('data-act="ir-label-save"'), 'the label has its own Save control');
    const none = renderSetup({ ...SETUP, irTarget: null, irLabel: null, backTo: 'settings' });
    assert.ok(/<select[^>]*data-act="ir-target"[^>]*disabled/.test(none), 'no target yet: the selector is disabled');
  }

  // test_ir_target_change_shows_mute_state_notice_before_save: nothing is sent until Save; Cancel leaves the target as it was.
  {
    const hass = makeHass({ admin: true, layout: withIr('a') });
    const c = quietController(hass); await settle(); await c.openSetup();
    c.pickIrZone('b');
    const sheet = c.view().sheet;
    assert.equal(sheet.irPending, 'b');
    assert.ok(!hass.calls.ws.some(m => m.type === 'bose_lifestyle/set_ir_zone'), 'nothing is sent before Save');
    const html = renderSettings(sheet);
    assert.ok(html.includes('The last-commanded mute state will be forgotten.'), 'the warning is shown before saving');
    assert.ok(html.indexOf('mute state will be forgotten') < html.indexOf('data-act="ir-save"'), 'the warning sits above Save');
    assert.ok(html.includes('data-act="ir-cancel"'));
    c.cancelIrZone();
    assert.equal(c.view().sheet.irPending, null);
    c.pickIrZone('b'); await c.saveIrZone();
    assert.deepEqual(hass.calls.ws.filter(m => m.type === 'bose_lifestyle/set_ir_zone'), [{ type: 'bose_lifestyle/set_ir_zone', zone: 'b' }]);
    assert.equal(c.view().irTarget.zone, 'b');
    assert.equal(c.view().sheet.irPending, null);
    c.pickIrZone('b');   // the current room needs no notice
    assert.equal(c.view().sheet.irPending, null);
  }

  // A failed save may still have been committed: the layout is read again so the room on screen is the bridge's.
  {
    const hass = makeHass({ admin: true, layout: withIr('a'), failWS: msg => msg.type === 'bose_lifestyle/set_ir_zone' });
    const c = quietController(hass); await settle(); await c.openSetup();
    c.pickIrZone('c');
    await c.saveIrZone();
    const at = hass.calls.ws.findIndex(m => m.type === 'bose_lifestyle/set_ir_zone');
    assert.ok(at >= 0 && hass.calls.ws.slice(at + 1).some(m => m.type === 'bose_lifestyle/layout'), 'the layout is read after a failed save');
    assert.ok(c.view().sheet.error && c.view().sheet.irPending === null);
  }

  // test_ir_receiver_label_validation_and_clear (card): trimmed and sent; a blank field clears the label (sent as null).
  {
    const hass = makeHass({ admin: true, layout: withIr('a') });
    const c = quietController(hass); await settle(); await c.openSetup();
    await c.setIrLabel('  Kitchen  ');
    assert.deepEqual(hass.calls.ws.at(-2), { type: 'bose_lifestyle/set_ir_receiver_label', label: 'Kitchen' });
    await c.setIrLabel('   ');
    assert.deepEqual(hass.calls.ws.at(-2), { type: 'bose_lifestyle/set_ir_receiver_label', label: null });
  }

  // test_config_uncertain_layout_shows_config_message_not_isnt_ready: rooms empty plus a reason shows the config message.
  {
    const hass = makeHass({ layout: { ...layoutFor([]), reason: 'config-uncertain', ir_target: null } });
    hass.states['sensor.health'].attributes = { fault: null, config: 'config-uncertain' };
    const c = quietController(hass); await settle();
    const view = c.view();
    assert.equal(view.message, MESSAGES.config_uncertain);
    assert.notEqual(view.message, "Bose Lifestyle Assistant isn't ready");
    const html = renderMain(view);
    assert.ok(!html.includes('isn&#39;t ready'), 'the page does not say not ready');
    assert.ok(html.includes(MESSAGES.config_uncertain.replace(/'/g, '&#39;')), 'the config message is shown, escaped');
  }

  // test_stale_alert_cleared_after_successful_action: a failed press leaves a notice; the next successful save clears it.
  {
    const hass = makeHass({ admin: true, layout: layoutFor(['a', 'b', 'c']) });
    const original = hass.callService.bind(hass);
    let failNext = true;
    hass.callService = async (...args) => { if (failNext) { const e = new Error(MESSAGES.not_sent); e.translation_key = 'not_sent'; throw e; } return original(...args); };
    const c = quietController(hass); await settle();
    await c.tap('button.a_mute', 'a', 'mute');
    assert.equal(c.view().notice, MESSAGES.not_sent);
    failNext = false;
    await c.openSetup();
    await c.setName('b', 'Garden');
    assert.equal(c.view().notice, null, 'a successful save clears the stale notice');
    assert.equal(c.view().sheet.error, null);
  }

  // test_alert_dismiss_control_present_and_labeled: the notice and the sheet error each have a Dismiss button; it clears them.
  {
    const hass = makeHass({ admin: true, layout: layoutFor(['a', 'b', 'c']) });
    const c = quietController(hass); await settle();
    c.notice = MESSAGES.uncertain;
    const main = renderMain(c.view());
    assert.ok(main.includes('data-act="dismiss"') && main.includes('>Dismiss<') && main.includes('aria-label="Dismiss message"'), 'labeled Dismiss on the notice');
    assert.ok(/class="ctl[^"]*"[^>]*data-act="dismiss"/.test(main) || /data-act="dismiss"[^>]*class="ctl/.test(main), 'the Dismiss control is a 44 px control');
    c.dismiss();
    assert.equal(c.view().notice, null);
    await c.openSetup();
    c.sheet.error = 'Something was not saved.';
    const setup = renderSettings(c.view().sheet);
    assert.ok(setup.includes('data-act="dismiss"') && setup.includes('>Dismiss<'), 'Dismiss on a sheet error too');
    c.dismiss();
    assert.equal(c.view().sheet.error, null);
  }

  // test_setup_back_button_names_destination: "Back to settings" from Settings; "Back to rooms" from Finish setup, which returns there.
  {
    assert.ok(renderSetup({ ...SETUP, backTo: 'settings' }).includes('>Back to settings<'));
    assert.ok(renderSetup({ ...SETUP, backTo: 'rooms' }).includes('>Back to rooms<'));
    const hass = makeHass({ admin: true, layout: layoutFor(['a', 'b', 'c']) });
    const c = quietController(hass); await settle();
    await c.openSettings(); await c.openSetup();
    assert.equal(c.view().sheet.backTo, 'settings');
    c.goBack();
    assert.equal(c.view().sheet.view, 'rooms', 'back returns to Settings');
    c.closeSettings();
    await c.openSetup();   // Finish setup opens with no Settings sheet behind it
    assert.equal(c.view().sheet.backTo, 'rooms');
    c.goBack();
    assert.equal(c.view().sheet, null, 'back from Finish setup returns to the rooms');
  }
  console.log('Card setup polish: IR target and label, notice before save, config-uncertain layout, stale alerts, dismiss and Back wording passed');
})().catch(error => { console.error(error); process.exit(1); });
