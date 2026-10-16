"""Config schema, allowlist, IR target resolution and fast-volume names (Task 3)."""
import _bootstrap
import itertools
import json
import unittest
from pathlib import Path

from bose_commands import CATALOG, COMMANDS, LIVE_ACTIONS
from bose_config import (ConfigError, SOURCES, ZONES, allowed_actions,
                         fast_volume_actions, parse_config, resolve_ir_action)

ROOT = Path(__file__).resolve().parents[1]


def make_config(enabled=(), connected=(), unverified=(), ir_zone='a'):
    return {
        'schema': 1,
        'zones': {z: {'enabled': z in enabled} for z in ZONES},
        'sources': {s: s in connected for s in SOURCES},
        'unverified': {z: z in unverified for z in ZONES},
        'ir': {'zone': ir_zone},
    }


def valid_bytes():
    return json.dumps(make_config(enabled=('a',)), separators=(',', ':')).encode('ascii')


class ParseRejects(unittest.TestCase):
    def assertRejected(self, data):
        with self.assertRaises(ConfigError, msg=repr(data)[:120]):
            parse_config(data)

    def test_parse_rejects(self):
        good = json.loads(valid_bytes())
        self.assertEqual(parse_config(valid_bytes()), good)

        self.assertRejected(b'{"schema":1,"schema":1,"zones":{},"sources":{},"unverified":{},"ir":{"zone":"a"}}')
        dup_nested = valid_bytes().replace(b'"enabled":true', b'"enabled":true,"enabled":false', 1)
        self.assertRejected(dup_nested)
        self.assertRejected(b'\xef\xbb\xbf' + valid_bytes())
        self.assertRejected(valid_bytes()[:-1] + b',}')
        self.assertRejected(b'[1]')
        self.assertRejected(b'"text"')
        self.assertRejected(valid_bytes().replace(b'"schema":1', b'"schema":true'))
        self.assertRejected(valid_bytes().replace(b'"schema":1', b'"schema":1.0'))
        self.assertRejected(valid_bytes().replace(b'"schema":1', b'"schema":2'))
        self.assertRejected(valid_bytes().replace(b'"schema":1', b'"schema":1,"extra":1'))
        self.assertRejected(valid_bytes().replace(b'"zone":"a"', b'"zone":"e"'))
        self.assertRejected(valid_bytes().replace(b'"zone":"a"', b'"zone":"A"'))
        self.assertRejected(valid_bytes().replace(b'"zone":"a"', b'"zone":null'))
        self.assertRejected(valid_bytes().replace(b'"enabled":true', b'"enabled":1', 1))
        self.assertRejected(valid_bytes().replace(b'"enabled":true', b'"enabled":"true"', 1))
        self.assertRejected(valid_bytes().replace(b',"ir":{"zone":"a"}', b''))
        self.assertRejected(valid_bytes().replace(b'"ir":{"zone":"a"}', b'"ir":{"zone":"a","x":1}'))
        self.assertRejected(valid_bytes().replace(b'"d":{"enabled":false}', b'"d":{"enabled":false,"x":1}'))
        self.assertRejected(valid_bytes().replace(b'"am":', b'"am2":'))
        self.assertRejected(valid_bytes().replace(b'"unverified":{', b'"unverified":{"e":false,'))
        self.assertRejected(b'{"schema":1,"zones":{"a":{"enabled":NaN}}}')
        self.assertRejected(b'{"schema":Infinity}')
        self.assertRejected(b'{"schema":-Infinity}')
        self.assertRejected(b'[' * 2000 + b']' * 2000)  # within 4096 bytes; hits the recursion limit
        self.assertRejected(b'\xff' + valid_bytes())
        self.assertRejected(valid_bytes() + b' ' * (4097 - len(valid_bytes())))
        self.assertRejected(None)

    def test_size_limit_is_inclusive_at_4096(self):
        base = valid_bytes()
        padded = base[:-1] + b' ' * (4096 - len(base)) + b'}'
        self.assertEqual(len(padded), 4096)
        parse_config(padded)
        with self.assertRaises(ConfigError):
            parse_config(padded[:-1] + b' ' * 1 + b'}')


class AllowlistMatrix(unittest.TestCase):
    def test_allowlist_matrix(self):
        zone_subsets = [set(c) for n in range(5) for c in itertools.combinations(ZONES, n)]
        self.assertEqual(len(zone_subsets), 16)
        source_sets = {'none': (), 'aux': ('aux',), 'all': SOURCES}
        unverified_sets = {'none': (), 'd': ('d',), 'all': ZONES}
        for enabled, (src_name, src), (unv_name, unv) in itertools.product(
                zone_subsets, source_sets.items(), unverified_sets.items()):
            config = make_config(enabled=enabled, connected=src, unverified=unv)
            allowed = allowed_actions(config)
            self.assertNotIn('identify', allowed)
            for name, entry in CATALOG.items():
                expected = (
                    entry.zone in enabled
                    and (entry.function != 'source' or entry.source in src)
                    and (entry.verified or entry.zone in unv)
                )
                self.assertEqual(name in allowed, expected,
                                 f'{name} enabled={sorted(enabled)} src={src_name} unv={unv_name}')
            self.assertEqual(allowed, frozenset(n for n in CATALOG if n in allowed))

    def test_zero_sources_keeps_power_mute_volume_only(self):
        allowed = allowed_actions(make_config(enabled=ZONES, connected=(), unverified=ZONES))
        self.assertFalse([n for n in allowed if CATALOG[n].function == 'source'])
        self.assertIn('room-a-power-on', allowed)
        self.assertIn('room-d-mute', allowed)
        self.assertIn('room-c-volume-up', allowed)
        self.assertIn('system-power-off', allowed)


class UnverifiedScope(unittest.TestCase):
    def test_unverified_only_affects_unverified_in_its_zone(self):
        base = allowed_actions(make_config(enabled=ZONES, connected=SOURCES))
        for zone in ZONES:
            with self.subTest(zone=zone):
                with_zone = allowed_actions(make_config(enabled=ZONES, connected=SOURCES, unverified=(zone,)))
                added = with_zone - base
                self.assertEqual(added, {n for n, e in CATALOG.items() if e.zone == zone and not e.verified})
                self.assertEqual(base - with_zone, frozenset())
        # zones A-C mix verified and unverified entries; the verified ones must already be in base
        self.assertTrue(all(n in base for n, e in CATALOG.items() if e.verified))


class IrResolution(unittest.TestCase):
    def test_resolve_ir_action(self):
        self.assertEqual(resolve_ir_action('volume-down', 'a', None), 'volume-down')
        for z in 'bcd':
            self.assertEqual(resolve_ir_action('volume-down', z, None), f'room-{z}-volume-down')
        for z in ZONES:
            self.assertEqual(resolve_ir_action('volume-up', z, None), f'room-{z}-volume-up')
            self.assertEqual(resolve_ir_action('mute', z, False), f'room-{z}-mute')
            self.assertEqual(resolve_ir_action('mute', z, True), f'room-{z}-unmute')
            self.assertEqual(resolve_ir_action('mute', z, None), f'room-{z}-mute')

    def test_every_key_zone_resolves_into_catalog(self):
        for key, zone in itertools.product(('volume-up', 'volume-down', 'mute'), ZONES):
            for state in (None, False, True):
                self.assertIn(resolve_ir_action(key, zone, state), CATALOG)

    def test_unknown_key_or_zone_raises(self):
        with self.assertRaises(ValueError):
            resolve_ir_action('source-fm', 'a', None)
        with self.assertRaises(ValueError):
            resolve_ir_action('mute', 'e', None)


class FastVolume(unittest.TestCase):
    def test_fast_volume_actions(self):
        self.assertEqual(fast_volume_actions('a'), frozenset({'room-a-volume-up', 'volume-down'}))
        for z in 'bcd':
            self.assertEqual(fast_volume_actions(z),
                             frozenset({f'room-{z}-volume-up', f'room-{z}-volume-down'}))
            self.assertTrue(fast_volume_actions(z) <= set(CATALOG))


if __name__ == '__main__':
    unittest.main()
