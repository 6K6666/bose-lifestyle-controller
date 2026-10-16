"""Documentation lint for docs/, README.md and NOTICE.

Run: python3 -B test_docs.py
Nothing here runs the CLI or the installer against the real system. The CLI lines in the docs are
parsed with bose_cli's own parser; the installer runs only with --root in a temporary folder.
Words that the public-tree scans forbid are assembled at run time, so this file never spells them.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import bose_cli  # noqa: E402
from public_manifest import public_files  # noqa: E402

DISPLAY_NAME = 'Bose Lifestyle Controller'
# The old name, matched only where it is not the bridge nickname "Bose Controller".
OLD_NAME = re.compile('Bose ' + r'Control(?!ler)')
FULL_LINE = ('Unofficial community project. Not affiliated with or endorsed by Bose Corporation. '
             '"Bose" and "Lifestyle" are trademarks of Bose Corporation, used here only to identify compatible products.')
SHORT_LINE = 'Home Assistant control for Bose Lifestyle 50 / M1 systems. Unofficial, not affiliated with Bose.'
DOCS = ROOT / 'docs'
REGISTRY = 'docs/RELEASE-PLACEHOLDERS.md'
PAGES = sorted([*DOCS.glob('*.md'), ROOT / 'README.md', ROOT / 'NOTICE'])
PAGE_RELS = [p.relative_to(ROOT).as_posix() for p in PAGES]
PI_ALLOWED = ('docs/example-raspberry-pi.md', 'docs/hardware.md')
PI_PAGE = 'example-' + 'raspberry-' + 'pi.md'
PI_WORD = re.compile(r'(?<![A-Za-z])' + 'P' + 'i' + r'(?![A-Za-z])|' + 'Rasp' + 'berry', re.IGNORECASE)
BRANDS = [''.join(parts) for parts in (('Phil', 'ips'), ('Direc', 'TV'), ('RC', '73'), ('Si', 'ri'))]
KEY = 'ssh-ed25519 AAAAPLACEHOLDERKEYBLOB'
SERIAL = '/dev/serial/by-id/usb-PLACEHOLDER-if00-port0'
RECEIVER = '/dev/input/by-id/usb-example-event-kbd'
BOSE_CMD = re.compile(r'(?:^|\s)(?:/opt/bose-lifestyle/bin/)?bose-lifestyle\s+(\S.*)$')
LINK = re.compile(r'\[[^\]]*\]\(([^)\s]+)\)')
PLACEHOLDER = re.compile(r'\[\[([A-Z][A-Z0-9-]*)\]\]')
LABELS = ('Where:', 'Expected:', 'If not:')
FORBIDDEN_SPELLING = re.compile(r'\b(labelled|colour|colours|behaviour|behaviours|centred|catalogue|favourite|licence|programme)\b', re.IGNORECASE)
FORBIDDEN_HISTORY = re.compile('|'.join(re.escape(''.join(p)) for p in (('migr', 'at'), ('legac', 'y'), ('histor', 'ical'), ('sch', 'ema'))), re.IGNORECASE)
RESTART_WORDS = re.compile(r'\b(restart|reboot|power[- ]cycle|unplug)', re.IGNORECASE)
BOSE_UNIT = re.compile(r'\b(M1|control unit|Bose unit)\b', re.IGNORECASE)
NEGATION = re.compile(r"\b(never|not|do not|don't|must not)\b", re.IGNORECASE)


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def fenced_lines(text):
    inside = False
    for line in text.splitlines():
        if line.strip().startswith('```'):
            inside = not inside
            continue
        if inside:
            yield line


def inline_codes(text):
    return re.findall(r'`([^`\n]+)`', text)


def headings(text):
    return [re.sub(r'[^\w\- ]', '', line.lstrip('#').strip().lower()).replace(' ', '-')
            for line in text.splitlines() if line.startswith('#')]


def cli_problem(rest):
    try:
        args = shlex.split(rest)
    except ValueError:
        return 'cannot be split into arguments'
    try:
        parsed = bose_cli._parser().parse_args(args)
    except bose_cli._UsageError as error:
        return 'usage: ' + str(error)
    except SystemExit as exit_request:
        return None if exit_request.code == 0 else 'exit ' + str(exit_request.code)
    if parsed.command is None:
        return 'no command'
    if parsed.command == 'config' and parsed.config_command is None:
        return 'config needs show'
    if parsed.command == 'ir' and parsed.ir_command is None:
        return 'ir needs a subcommand'
    return None


class DocsTests(unittest.TestCase):
    def test_every_cli_command_in_docs_exists_in_cli_help(self):
        checked = 0
        failures = []
        for rel in PAGE_RELS:
            text = read(rel)
            sources = [line.strip() for line in fenced_lines(text)] + inline_codes(text)
            for source in sources:
                match = BOSE_CMD.search(source)
                if not match:
                    continue
                checked += 1
                problem = cli_problem(match.group(1))
                if problem:
                    failures.append(f'{rel}: {source!r}: {problem}')
        self.assertGreaterEqual(checked, 15, 'too few CLI examples found; the scan is broken')
        self.assertEqual(failures, [])

    def test_every_internal_link_resolves(self):
        failures = []
        for rel in PAGE_RELS:
            for target in LINK.findall(read(rel)):
                if target.startswith(('http://', 'https://', 'mailto:')):
                    continue
                path, _, anchor = target.partition('#')
                dest = (ROOT / rel).parent / path if path else ROOT / rel
                if not dest.exists():
                    failures.append(f'{rel}: missing {target}')
                    continue
                if anchor and dest.suffix == '.md' and anchor not in headings(dest.read_text(encoding='utf-8')):
                    failures.append(f'{rel}: no heading for {target}')
        self.assertEqual(failures, [])

    def test_no_private_strings_or_brands_outside_allowed_pages(self):
        shapes = [line for line in read('tools/neutral_shapes.txt').splitlines() if line.strip() and not line.startswith('#')]
        brand = re.compile('|'.join(re.escape(b) for b in BRANDS), re.IGNORECASE)
        literals = [line for line in Path(os.environ['BOSE_PRIVATE_LITERALS']).read_text(encoding='utf-8').splitlines()
                    if line.strip()] if os.environ.get('BOSE_PRIVATE_LITERALS') else []
        found = []
        for rel in PAGE_RELS:
            for n, line in enumerate(read(rel).splitlines(), 1):
                if any(re.search(shape, line) for shape in shapes) or brand.search(line):
                    found.append(f'{rel}:{n}')
                if any(lit.lower() in line.lower() for lit in literals):
                    found.append(f'{rel}:{n} (private literal)')
        self.assertEqual(found, [])

    def test_install_doc_steps_have_where_expected_if_not(self):
        text = read('docs/install.md')
        blocks, current = [], None
        for line in text.splitlines():
            if line.startswith('### Step '):
                current = []
                blocks.append(current)
            elif line.startswith('#'):
                current = None
            elif current is not None:
                current.append(line)
        self.assertGreaterEqual(len(blocks), 15, 'install steps are missing')
        for index, block in enumerate(blocks, 1):
            found = [label for line in block for label in LABELS if line.startswith(label)]
            self.assertEqual(found, list(LABELS), f'step {index} needs exactly Where, Expected and If not, in order')

    def test_uninstall_covers_every_file_install_creates(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = tmp / 'root'
        (root / 'home' / 'bose').mkdir(parents=True)   # the account's home exists before the installer runs
        for node in (SERIAL, RECEIVER):
            (root / node.lstrip('/')).parent.mkdir(parents=True, exist_ok=True)
            (root / node.lstrip('/')).write_bytes(b'')
        result = subprocess.run(
            ['sh', str(ROOT / 'deploy' / 'install-bridge.sh'), '--root', str(root), '--public-key', KEY,
             '--no-systemctl', '--skip-prereq-check', '--serial-device', SERIAL, '--receiver-device', RECEIVER,
             '--receiver-vendor', 'abcd', '--receiver-product', '0123'],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        uninstall = read('docs/uninstall.md')
        created = []
        for path in root.rglob('*'):
            rel = path.relative_to(root).as_posix()
            if rel == 'dev' or rel.startswith('dev/'):
                continue                       # the fake device nodes this test created
            created.append('/' + rel)
        self.assertTrue(created)
        # Shared system folders are not ours to remove, so naming one of them never covers a created path.
        shared = {'/', '/etc', '/etc/systemd', '/etc/systemd/system', '/etc/udev', '/etc/udev/rules.d',
                  '/var', '/var/lib', '/opt', '/home', '/home/bose'}
        missing = []
        for path in created:
            if path in shared:
                continue
            parts = path.split('/')
            ancestors = ['/'.join(parts[:i]) for i in range(2, len(parts) + 1)]
            ancestors = [a for a in ancestors if a not in shared]
            if path not in uninstall and not any(a in uninstall for a in ancestors):
                missing.append(path)
        self.assertEqual(missing, [], 'uninstall.md does not remove these installer-created paths')
        required = [
            '/opt/bose-lifestyle', '/var/lib/bose-control', 'config.json', 'ir-config.json', 'ir-state.json',
            'events.jsonl', 'events.jsonl.1', '/run/bose-control', '/etc/systemd/system/bose-bridge.service',
            '/etc/udev/rules.d/70-bose-receiver.rules', '/home/bose/.ssh/authorized_keys',
            'command="/opt/bose-lifestyle/bose_request.py",restrict', '__pycache__',
            'custom_components/bose_lifestyle', '.ssh/bose_ed25519', '.ssh/bose_known_hosts', '.ssh/bose-',
            '.storage/bose_lifestyle.rooms',
        ]
        self.assertEqual([item for item in required if item not in uninstall], [])

    def test_required_disclaimers_present(self):
        for rel in ('README.md', 'NOTICE'):
            text = read(rel).lower()
            self.assertIn('unofficial', text, rel)
            self.assertIn('not affiliated', text, rel)
        self.assertIn('Never power-cycle the M1 control unit', read('docs/start-here.md'))
        hardware = read('docs/hardware.md')
        self.assertIn('Never power-cycle the M1 control unit', hardware)
        self.assertIn('TTL', hardware)
        self.assertIn('Never connect an RS-232 level device', hardware)

    def test_readme_contains_full_disclaimer_line_verbatim(self):
        self.assertIn(FULL_LINE, read('README.md'))
        self.assertIn(FULL_LINE, read('docs/start-here.md'))

    def test_short_disclaimer_line_registered_for_repo_description(self):
        self.assertIn(f'GitHub repository description: {SHORT_LINE}', read(REGISTRY))

    def test_unofficial_wording(self):
        found = []
        for rel in [*PAGE_RELS, 'custom_components/bose_lifestyle/manifest.json', 'hacs.json']:
            text = read(rel)
            for match in re.finditer(r'official', text, re.IGNORECASE):
                if text[max(0, match.start() - 2):match.start()].lower() == 'un':
                    continue
                window = text[max(0, match.start() - 120):match.end() + 120].lower()
                if 'unofficial' not in window and 'not affiliated' not in window:
                    found.append(f'{rel}:{text.count(chr(10), 0, match.start()) + 1}')
        self.assertEqual(found, [])

    def test_display_name_single_source(self):
        manifest = json.loads(read('custom_components/bose_lifestyle/manifest.json'))['name']
        hacs = json.loads(read('hacs.json'))['name']
        title = next(line[2:] for line in read('README.md').splitlines() if line.startswith('# '))
        card = read('custom_components/bose_lifestyle/frontend/bose-control-card.js')
        card_name = re.search(r"window\.customCards\.push\(\{[^}]*name: '([^']+)'", card).group(1)
        self.assertEqual({manifest, hacs, title, card_name}, {DISPLAY_NAME})

    def test_no_old_display_name_left_except_identifiers(self):
        found = []
        for rel in public_files(ROOT):
            if rel.endswith(('.png', '.jpg', '.gif', '.ico', '.zip', '.gz')):
                continue
            text = (ROOT / rel).read_text(encoding='utf-8')
            for n, line in enumerate(text.splitlines(), 1):
                if OLD_NAME.search(line):
                    found.append(f'{rel}:{n}')
        self.assertEqual(found, [])

    def test_raspberry_pi_only_in_allowed_docs(self):
        for rel in PI_ALLOWED:
            self.assertTrue((ROOT / rel).exists(), rel)
        found = []
        for rel in PAGE_RELS:
            if rel in PI_ALLOWED:
                continue
            for n, line in enumerate(read(rel).splitlines(), 1):
                if PI_WORD.search(line.replace(PI_PAGE, '')):
                    found.append(f'{rel}:{n}')
        self.assertEqual(found, [])

    def test_placeholders_listed(self):
        rows = {}
        for line in read(REGISTRY).splitlines():
            if not line.startswith('|'):
                continue
            cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
            match = PLACEHOLDER.search(cells[0])
            if match:
                self.assertEqual(len(cells), 4, line)
                self.assertTrue(all(cells), line)
                rows[match.group(1)] = cells
        problems = []
        for rel in PAGE_RELS:
            if rel == REGISTRY:
                continue
            for name in PLACEHOLDER.findall(read(rel)):
                if name not in rows:
                    problems.append(f'{rel}: {name} is not in {REGISTRY}')
                elif Path(rel).name not in rows[name][1]:
                    problems.append(f'{rel}: {name} is not listed as used on this page')
        self.assertEqual(problems, [])

    def test_american_spelling(self):
        found = []
        for rel in PAGE_RELS:
            for n, line in enumerate(read(rel).splitlines(), 1):
                if FORBIDDEN_SPELLING.search(line):
                    found.append(f'{rel}:{n}')
        self.assertEqual(found, [])

    def test_ssh_missing_section_exists_and_matches_config_flow_text(self):
        heading = '## Home Assistant without ssh'
        self.assertIn(heading, read('docs/install.md').splitlines())
        for rel in ('custom_components/bose_lifestyle/strings.json',
                    'custom_components/bose_lifestyle/translations/en.json'):
            text = read(rel)
            self.assertIn("'Home Assistant without ssh'", text, rel)
            for key in ('"ssh_missing"', '"ssh_keyscan_missing"'):
                self.assertIn(key, text, rel)

    def test_no_restart_reboot_or_power_cycle_for_the_bose_unit(self):
        found = []
        for rel in PAGE_RELS:
            for n, line in enumerate(read(rel).splitlines(), 1):
                if RESTART_WORDS.search(line) and BOSE_UNIT.search(line) and not NEGATION.search(line):
                    found.append(f'{rel}:{n}')
        self.assertEqual(found, [])

    def test_no_history_or_official_wording(self):
        found = []
        for rel in PAGE_RELS:
            for n, line in enumerate(read(rel).splitlines(), 1):
                if FORBIDDEN_HISTORY.search(line) or re.search(r'(?<!un)official', line, re.IGNORECASE):
                    found.append(f'{rel}:{n}')
        self.assertEqual(found, [])

    def test_install_doc_flags_exist_in_installer(self):
        installer_flags = set(re.findall(r'--[a-z][a-z-]*', read('deploy/install-bridge.sh')))
        unknown = []
        for rel in PAGE_RELS:
            for line in fenced_lines(read(rel)):
                if 'install-bridge.sh' in line:
                    for flag in re.findall(r'--[a-z][a-z-]*', line):
                        if flag not in installer_flags:
                            unknown.append(f'{rel}: {flag}')
        self.assertEqual(unknown, [])


    def test_docs_learn_commands_parse(self):
        found, failures = [], []
        for rel in PAGE_RELS:
            text = read(rel)
            for source in [line.strip() for line in fenced_lines(text)] + inline_codes(text):
                match = BOSE_CMD.search(source)
                if match and re.search(r'\bir learn\b', match.group(1)):
                    found.append(rel)
                    problem = cli_problem(match.group(1))
                    if problem:
                        failures.append(f'{rel}: {source!r}: {problem}')
        self.assertGreaterEqual(len(found), 2, 'ir learn must be shown on the install page and the receivers page')
        self.assertEqual(failures, [])


if __name__ == '__main__':
    unittest.main()
