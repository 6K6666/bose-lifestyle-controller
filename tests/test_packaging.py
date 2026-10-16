"""Packaging for HACS and Home Assistant (release plan, Task 2).

Run: python3 -B tests/test_packaging.py
Requirements come from maintainer/release/hacs-requirements.md. Placeholder values are registered in
docs/RELEASE-PLACEHOLDERS.md and must be replaced at release.
"""
import _bootstrap
import hashlib
import json
import re
import struct
import sys
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from build_release import default_version  # noqa: E402
from public_manifest import DISPLAY_NAME, is_excluded, public_files  # noqa: E402

COMP = ROOT / 'custom_components' / 'bose_lifestyle'
COMP_BRAND = COMP / 'brand'
BRAND = ROOT / 'brand'
BRAND_SIZES = {'icon.png': (256, 256), 'icon@2x.png': (512, 512), 'logo.png': (640, 160),
               'logo@2x.png': (1280, 320), 'dark_logo.png': (640, 160), 'dark_logo@2x.png': (1280, 320)}
PAGE_SIZES = {'docs/assets/banner.png': (1280, 320), 'docs/assets/social.png': (1200, 630)}
REGISTRY = ROOT / 'docs' / 'RELEASE-PLACEHOLDERS.md'
PROJECT_URL = 'https://github.com/6K6666/bose-lifestyle-controller'
OWNER = '@6K6666'
IOT_CLASSES = {'assumed_state', 'cloud_polling', 'cloud_push', 'local_polling', 'local_push', 'calculated'}
INTEGRATION_TYPES = {'device', 'entity', 'hardware', 'helper', 'hub', 'service', 'system', 'virtual'}
HACS_KEYS = {'name', 'content_in_root', 'zip_release', 'filename', 'hide_default_branch', 'country',
             'homeassistant', 'hacs', 'persistent_directory'}
SEMVER = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+')
PIP_STRING = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*(\[[^\]]+\])?((==|>=|<=|~=|!=|>|<)[^\s,;]+)?')
PINNED_HA = (ROOT / 'requirements-ha-test.txt').read_text(encoding='utf-8').strip().partition('==')[2]
WORKFLOWS = {name: (ROOT / '.github' / 'workflows' / name).read_text(encoding='utf-8')
             for name in ('validate.yml', 'tests.yml')}


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def png_chunks(data):
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise AssertionError('not a PNG')
    pos, chunks = 8, []
    while pos < len(data):
        (length,) = struct.unpack('>I', data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        crc = struct.unpack('>I', data[pos + 8 + length:pos + 12 + length])[0]
        if crc != zlib.crc32(tag + body) & 0xFFFFFFFF:
            raise AssertionError(f'bad CRC in {tag!r}')
        chunks.append((tag, body))
        pos += 12 + length
    return chunks


def png_header(data):
    chunks = png_chunks(data)
    assert chunks[0][0] == b'IHDR' and chunks[-1][0] == b'IEND'
    width, height, depth, color = struct.unpack('>IIBB', chunks[0][1][:10])
    return width, height, depth, color


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(read('custom_components/bose_lifestyle/manifest.json'))

    def test_manifest_has_required_keys_and_valid_values(self):
        m = self.manifest
        required = {'domain', 'name', 'version', 'documentation', 'issue_tracker', 'codeowners',
                    'config_flow', 'iot_class', 'integration_type', 'requirements'}
        self.assertLessEqual(required, set(m))
        self.assertEqual(m['domain'], 'bose_lifestyle')
        self.assertEqual(m['name'], DISPLAY_NAME)
        self.assertRegex(m['version'], r'\A[0-9]+\.[0-9]+\.[0-9]+\Z')
        self.assertEqual(m['documentation'], PROJECT_URL)
        self.assertEqual(m['issue_tracker'], PROJECT_URL + '/issues')
        self.assertEqual(m['codeowners'], [OWNER])
        self.assertIs(m['config_flow'], True)
        self.assertIn(m['iot_class'], IOT_CLASSES)
        self.assertIn(m['integration_type'], INTEGRATION_TYPES)
        self.assertIsInstance(m['requirements'], list)
        for requirement in m['requirements']:
            self.assertRegex(requirement, PIP_STRING)
        keys = list(m)
        self.assertEqual(keys[:2], ['domain', 'name'])
        self.assertEqual(keys[2:], sorted(keys[2:]))

    def test_manifest_points_nowhere_in_core(self):
        self.assertNotIn('home-assistant/core', read('custom_components/bose_lifestyle/manifest.json'))


class HacsTests(unittest.TestCase):
    def test_hacs_json_valid(self):
        data = json.loads(read('hacs.json'))
        self.assertEqual(data['name'], DISPLAY_NAME)
        self.assertLessEqual(set(data), HACS_KEYS)
        self.assertEqual(data['homeassistant'], PINNED_HA)
        self.assertNotIn('render_readme', data)
        self.assertNotIn('zip_release', data)

    def test_versions_agree(self):
        version = read('VERSION').strip()
        self.assertRegex(version, r'\A' + SEMVER.pattern + r'\Z')
        self.assertEqual(read('VERSION'), version + '\n')
        manifest = json.loads(read('custom_components/bose_lifestyle/manifest.json'))
        self.assertEqual(manifest['version'], version)
        heads = [h for h in re.findall(r'^## \[([^\]]+)\]', read('CHANGELOG.md'), re.M) if h != 'Unreleased']
        self.assertEqual(heads[0], version)
        self.assertEqual(default_version(ROOT), version)
        self.assertNotIn('version', json.loads(read('hacs.json')))

    def test_released_packaging_has_no_unfilled_placeholders(self):
        registry = read('docs/RELEASE-PLACEHOLDERS.md')
        self.assertNotIn('example.invalid/bose-lifestyle', registry)
        self.assertNotIn('PLACEHOLDER-OWNER', registry)
        self.assertNotIn('RELEASE-DATE', registry)
        for rel in ('custom_components/bose_lifestyle/manifest.json', 'CHANGELOG.md', 'hacs.json'):
            self.assertEqual(re.findall(r'\[\[[^\]]+\]\]', read(rel)), [], rel)

    def test_gitignore_in_archive_and_clean(self):
        self.assertFalse(is_excluded('.gitignore'))
        shapes = [re.compile(line) for line in read('tools/neutral_shapes.txt').splitlines()
                  if line.strip() and not line.startswith('#')]
        for line in read('.gitignore').splitlines():
            self.assertFalse(any(rx.search(line) for rx in shapes), 'a .gitignore pattern matches a private shape')


class BrandTests(unittest.TestCase):
    def test_brand_png_dimensions_and_alpha(self):
        self.assertEqual(sorted(p.name for p in COMP_BRAND.iterdir()), sorted(BRAND_SIZES))
        for name, size in BRAND_SIZES.items():
            width, height, depth, color = png_header((COMP_BRAND / name).read_bytes())
            self.assertEqual((width, height), size, name)
            self.assertEqual((depth, color), (8, 6), f'{name} must be 8-bit RGBA')
        self.assertFalse(list(COMP_BRAND.glob('dark_icon*')))

    def test_brand_files_match_root_mirror(self):
        self.assertEqual(sorted(p.name for p in BRAND.iterdir()), ['icon.png', 'icon@2x.png'])
        for name in ('icon.png', 'icon@2x.png'):
            self.assertEqual((BRAND / name).read_bytes(), (COMP_BRAND / name).read_bytes(), name)

    def test_icon_pngs_unchanged_by_rename(self):
        # The display-name rename must not touch the icon artwork; these hashes pin it.
        pins = {'custom_components/bose_lifestyle/brand/icon.png': '34a7c9c45cbeddedee57ddcdc752c0a40a75366d640b375d5e2fd582b7f4f860',
                'custom_components/bose_lifestyle/brand/icon@2x.png': '98fec38807c331df120e743d7a5af7ef766425a09c074ca30c877b4d6f9ddce2',
                'brand/icon.png': '34a7c9c45cbeddedee57ddcdc752c0a40a75366d640b375d5e2fd582b7f4f860',
                'brand/icon@2x.png': '98fec38807c331df120e743d7a5af7ef766425a09c074ca30c877b4d6f9ddce2',
                'docs/assets/icon-small.png': 'de6241441d1aa81c3001e302951ea2ec24f70769d5c27e29ea2baaec075b8bb3'}
        for rel, digest in pins.items():
            self.assertEqual(hashlib.sha256((ROOT / rel).read_bytes()).hexdigest(), digest, rel)
        for svg in (ROOT / 'maintainer' / 'branding').glob('*.svg'):
            self.assertIn('Bose Lifestyle Assistant', svg.read_text(), svg.name)

    def test_at2x_exemption_is_narrow(self):
        # Probe addresses are assembled at run time, so this file never spells an e-mail address.
        shape = next(line for line in read('tools/neutral_shapes.txt').splitlines()
                     if '@' in line and line.strip() and not line.startswith('#'))
        rx = re.compile(shape)
        for name in ('brand/icon@2x.png', 'custom_components/bose_lifestyle/brand/dark_logo@2x.png'):
            self.assertIsNone(rx.search(name), name)
        for address in ('owner' + '@example.com', 'a' + '@2x.co'):
            self.assertIsNotNone(rx.search(address), 'the e-mail shape must still match real addresses')

    def test_no_icon_generator_in_public_tree(self):
        self.assertFalse((ROOT / 'tools' / 'make_icon.py').exists())
        self.assertFalse((BRAND / 'icon.svg').exists())
        self.assertFalse([rel for rel in public_files(ROOT) if re.search(r'make_icon|make_brand|export\.sh', rel)])

    def test_banner_and_social_present_and_sizes(self):
        for rel, size in PAGE_SIZES.items():
            width, height, _depth, _color = png_header((ROOT / rel).read_bytes())
            self.assertEqual((width, height), size, rel)
        self.assertTrue((ROOT / 'docs' / 'assets' / 'icon-small.png').is_file())

    def test_maintainer_branding_excluded_from_archive(self):
        source = ROOT / 'maintainer' / 'branding' / 'BRANDING.md'
        if (ROOT / 'maintainer').exists():
            self.assertTrue(source.is_file())
        self.assertTrue(is_excluded('maintainer/branding/BRANDING.md'))
        self.assertFalse([rel for rel in public_files(ROOT) if rel.startswith('maintainer/branding/')])


class WorkflowTests(unittest.TestCase):
    def uses(self, text):
        return re.findall(r'^\s*(?:-\s*)?uses:\s*(\S+)', text, re.M)

    def test_workflows_have_expected_triggers_and_minimal_permissions(self):
        validate, tests = WORKFLOWS['validate.yml'], WORKFLOWS['tests.yml']
        for trigger in ('push:', 'pull_request:', 'schedule:', 'workflow_dispatch:'):
            self.assertIn('  ' + trigger, validate)
        self.assertIn('cron:', validate)
        self.assertRegex(validate, r'hacs/action@[0-9a-f]{40}')
        self.assertRegex(validate, r'category:\s*integration')
        self.assertRegex(validate, r'home-assistant/actions/hassfest@[0-9a-f]{40}')
        self.assertIn('permissions: {}', validate)
        self.assertIn('./run_tests.sh --stdlib-only', tests)
        self.assertIn('requirements-ha-test.txt', tests)
        self.assertIn('permissions:\n  contents: read', tests)
        for text in WORKFLOWS.values():
            self.assertNotIn('pull_request_target', text)
            self.assertNotIn('secrets.', text)
            self.assertIsNone(re.search(r':\s*write\b', text))
            for ref in self.uses(text):
                self.assertRegex(ref, r'^[^@]+@[0-9a-f]{40}$', ref)


class RepositoryLayoutTests(unittest.TestCase):
    def test_public_test_suites_live_under_tests(self):
        self.assertEqual(list(ROOT.glob('test_*')), [])
        self.assertTrue(list((ROOT / 'tests').glob('test_*')))


if __name__ == '__main__':
    unittest.main()
