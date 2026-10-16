"""One command for every release check (release plan, Task 3).

python3 tools/release_gate.py [--patterns PRIVATE_LIST] [--archive PATH] [--review-hash SHA]
    [--results build/test-results.json] [--root .] [--release-identity EMAIL]
    [--deny-identity EMAIL ...] [--strict]

Prints one PASS, FAIL or SKIPPED line per check, then a summary. A reason names a check or a count
and never echoes matched text or a private literal. Exit 0 only when no check failed; with --strict,
a skipped check fails the gate too (release mode: private list and reviewed archive hash given).
A check that raises fails with the exception class only, so no path or content leaks.
Checks judge committed content: files are read from HEAD (git show), never from the working tree, and a dirty
working tree fails the gate (tree-clean). The results record is the one generated file the gate reads.
"""
import argparse
import fnmatch
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from build_release import ARCHIVE_PREFIX, build_release, default_version, scan_archive  # noqa: E402
from public_manifest import DISPLAY_NAME, public_entries  # noqa: E402

FULL_DISCLAIMER = ('Unofficial community project. Not affiliated with or endorsed by Bose Corporation. '
                   '"Bose" and "Lifestyle" are trademarks of Bose Corporation, used here only to identify compatible products.')
COMP = 'custom_components/bose_lifestyle'
BRAND_FILES = ['brand/icon.png', 'brand/icon@2x.png'] + [f'{COMP}/brand/{n}' for n in (
    'icon.png', 'icon@2x.png', 'logo.png', 'logo@2x.png', 'dark_logo.png', 'dark_logo@2x.png')]
MIRRORS = [('brand/icon.png', f'{COMP}/brand/icon.png'), ('brand/icon@2x.png', f'{COMP}/brand/icon@2x.png')]
REGISTRY_REL = 'docs/RELEASE-PLACEHOLDERS.md'
RESULTS_REL = 'build/test-results.json'
RUNNER = 'run_tests.sh'
FORBIDDEN_MEMBERS = ('maintainer/', 'reports/', 'backups/', 'dist/', 'docs/superpowers/', 'build/')
# Built-in floor for the placeholder scan: the registered values as they are written in the
# registry, spelled by concatenation so this file never contains them. A bare host name is not
# a placeholder: test data may use one.
FLOOR_NEEDLES = ('https://' + 'example.invalid/bose-lifestyle', '@' + 'PLACEHOLDER-OWNER')
# The token shape test_docs.py uses for registered placeholders.
TOKEN = re.compile(r'\[\[[A-Z][A-Z0-9-]*\]\]')
MARKER_RE = re.compile(r'\b(?:' + '|'.join(['TO' + 'DO', 'FIX' + 'ME', 'X' + 'XX']) + r')\b')
DEFAULT_IDENTITY = re.compile(
    r'^noreply@|\.noreply\.github\.com$|\.local$|@localhost$|@example\.(?:com|org|net|invalid)$'
    r'|^(?:root|user|test|nobody)@', re.IGNORECASE)
GITHUB_NOREPLY = re.compile(r'(?:[0-9]+\+)?[A-Za-z0-9-]+@users\.noreply\.github\.com', re.IGNORECASE)
SEPARATOR = re.compile(r'\|(\s*:?-+:?\s*\|)+')
BACKTICK = re.compile(r'`([^`]+)`')


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout


def registry_placeholder_rows(text):
    """Rows from the table whose first column is Placeholder, excluding later checklist tables."""
    lines = text.splitlines()
    for index in range(len(lines) - 1):
        if not lines[index].startswith('|') or not SEPARATOR.fullmatch(lines[index + 1].strip()):
            continue
        headers = [cell.strip().strip('`') for cell in lines[index].strip('|').split('|')]
        if not headers or headers[0] != 'Placeholder':
            continue
        rows = []
        for line in lines[index + 2:]:
            if not line.startswith('|'):
                break
            rows.append(line)
        return rows
    return []


def is_text(data):
    return b'\0' not in data


@dataclass
class Gate:
    root: Path
    scratch: Path
    patterns: Optional[Path] = None
    archive: Optional[Path] = None
    review_hash: Optional[str] = None
    results: Optional[Path] = None
    release_identity: Optional[str] = None
    deny_identity: List[str] = field(default_factory=list)
    _built: Dict[str, object] = field(default_factory=dict)

    def head_sha(self):
        return git(self.root, 'rev-parse', 'HEAD').strip()

    def build(self):
        if 'head' not in self._built:
            self._built['head'] = build_release(self.root, self.scratch / 'head', default_version(self.root))
        return self._built['head']

    def target(self):
        """The archive under test: --archive when given, otherwise a fresh build of HEAD."""
        return self.archive if self.archive else self.build().archive

    def head_files(self):
        """(path, bytes) for every public file at HEAD, the content the archive ships."""
        for rel, _mode, sha in public_entries(self.root):
            yield rel, subprocess.run(['git', '-C', str(self.root), 'cat-file', 'blob', sha],
                                      check=True, capture_output=True).stdout

    def head_bytes(self, rel):
        """Committed content of rel at HEAD, or None when HEAD has no such file."""
        run = subprocess.run(['git', '-C', str(self.root), 'show', f'HEAD:{rel}'], capture_output=True)
        return run.stdout if run.returncode == 0 else None

    def head_text(self, rel):
        data = self.head_bytes(rel)
        if data is None:
            raise FileNotFoundError(rel)
        return data.decode('utf-8')

    def head_paths(self):
        return git(self.root, 'ls-tree', '-r', '--name-only', 'HEAD').splitlines()

    def head_tree(self):
        """A clone of HEAD in scratch, for the checks that run a script from the repo (test_docs.py lists tracked
        files with git, so it needs a repository, not an archive extract)."""
        target = self.scratch / 'head-tree'
        if not target.exists():
            git(self.root, 'clone', '--quiet', '--no-hardlinks', '--no-checkout', str(self.root), str(target))
            git(target, 'checkout', '--quiet', '--detach', self.head_sha())
        return target

    def record(self):
        path = self.root / (self.results or RESULTS_REL)
        if not path.is_file():
            return None, 'no results file (run ./run_tests.sh first)'
        try:
            return json.loads(path.read_text(encoding='utf-8')), ''
        except ValueError:
            return None, 'results file is not valid JSON'


def _with_record(gate, check):
    record, why = gate.record()
    if record is None:
        return False, why
    return check(record)


def c_results_file(g):
    record, why = g.record()
    return record is not None, why


def c_results_mode(g):
    return _with_record(g, lambda r: (r.get('mode') == 'full', f"mode is '{r.get('mode')}', need full"))


def c_results_head(g):
    return _with_record(g, lambda r: (r.get('head') == g.head_sha(), 'recorded for a different commit'))


def c_results_clean(g):
    return _with_record(g, lambda r: (r.get('dirty') is False, 'recorded from a dirty tree'))


def c_tree_clean(g):
    dirty = git(g.root, 'status', '--porcelain').splitlines()
    return not dirty, f'{len(dirty)} uncommitted change(s); commit or stash before the gate'


def expected_suites(g):
    """Full-mode suite names, recomputed from run_tests.sh at HEAD (its NAME="..." lists and maintainer glob)."""
    # A trailing comment after the closing quote is allowed (MAINTAINER_VENV_GLOB carries one).
    lists = dict(re.findall(r'^([A-Z_]+)="([^"]*)"[ \t]*(?:#.*)?$', g.head_text(RUNNER), re.M))
    names = []
    for key in ('STDLIB_SUITES', 'CARD_SUITE', 'VENV_SUITES'):
        names += lists.get(key, '').split()
    for key in ('MAINTAINER_GLOB', 'MAINTAINER_VENV_GLOB'):
        glob = lists.get(key, '')
        names += [p for p in g.head_paths()
                  if glob and fnmatch.fnmatchcase(p, glob) and p.count('/') == glob.count('/')]
    return names


def c_results_suites(g):
    def check(r):
        suites = r.get('suites') or []
        if not suites:
            return False, 'no suites recorded'
        if r.get('runner') != RUNNER:
            return False, 'record was not written by run_tests.sh'
        if r.get('runner_sha256') != hashlib.sha256(g.head_bytes(RUNNER) or b'').hexdigest():
            return False, 'record was made from a different run_tests.sh than HEAD'
        expected = expected_suites(g)
        if r.get('planned') != len(expected):
            return False, f'run_tests.sh plans {len(expected)} suite(s); the record says {r.get("planned")}'
        passed = {s.get('name') for s in suites if s.get('exit') == 0}
        missing = [name for name in expected if name not in passed]
        if missing:
            return False, f'{len(missing)} expected suite(s) missing or not exit 0'
        failed = sum(1 for s in suites if s.get('exit') != 0)
        return failed == 0, f'{failed} suite(s) did not exit 0'
    return _with_record(g, check)


def c_neutrality(g):
    if g.patterns is None:
        return None, 'no private list given (--patterns)'
    if not g.patterns.is_file():
        return False, 'private list cannot be read'
    literals = [ln for ln in g.patterns.read_text(encoding='utf-8').splitlines()
                if ln.strip() and not ln.startswith('#')]
    if not literals:
        return False, 'private list has no entries'
    findings = scan_archive(g.build().archive, g.patterns)
    return not findings, f'{len(findings)} finding(s) with the private list'


def c_placeholders(g):
    registry = g.head_bytes(REGISTRY_REL)
    if registry is None:
        return False, 'registry file missing'
    rows = registry_placeholder_rows(registry.decode('utf-8'))
    needles = set(FLOOR_NEEDLES)
    for row in rows:
        needles.update(BACKTICK.findall(row))
    hits = 0
    for rel, data in g.head_files():
        if rel == REGISTRY_REL or not is_text(data):
            continue
        text = data.decode('utf-8', 'replace')
        hits += len(TOKEN.findall(text)) + sum(text.count(needle) for needle in needles if needle)
    problems = []
    if rows:
        problems.append(f'{len(rows)} registry row(s) remain')
    if hits:
        problems.append(f'{hits} placeholder occurrence(s) in public files')
    return not problems, '; '.join(problems)


def c_docs_lint(g):
    tree = g.head_tree()
    script = tree / 'test_docs.py'
    if not script.is_file():
        return False, 'test_docs.py missing'
    run = subprocess.run([sys.executable, '-B', str(script)], cwd=str(tree), capture_output=True)
    return run.returncode == 0, f'docs lint exit {run.returncode}'


def c_archive_build(g):
    try:
        g.build()
    except ValueError as exc:
        return False, str(exc) if str(exc).startswith('archive scan failed') else 'build refused'
    return True, ''


def c_archive_reproducible(g):
    first = g.build()
    second = build_release(g.root, g.scratch / 'again', default_version(g.root))
    return first.sha256 == second.sha256, 'two builds of HEAD differ'


def c_archive_scan(g):
    findings = scan_archive(g.target())
    return not findings, f'{len(findings)} finding(s) in the archive'


def c_archive_members(g):
    with zipfile.ZipFile(g.target()) as zf:
        names = zf.namelist()
    bad = [n for n in names if n.startswith(FORBIDDEN_MEMBERS) or '.git' in n.split('/')]
    return not bad, f'{len(bad)} forbidden member(s)'


def c_versions(g):
    version = g.head_text('VERSION').strip()
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        return False, 'VERSION is not X.Y.Z'
    disagree = []
    if json.loads(g.head_text(f'{COMP}/manifest.json')).get('version') != version:
        disagree.append('manifest')
    heads = [h for h in re.findall(r'^## \[([^\]]+)\]', g.head_text('CHANGELOG.md'), re.M)
             if h != 'Unreleased']
    if not heads or heads[0] != version:
        disagree.append('CHANGELOG')
    hacs = json.loads(g.head_text('hacs.json'))
    if 'version' in hacs and hacs['version'] != version:
        disagree.append('hacs.json')
    if g.archive is not None and g.archive.name != f'{ARCHIVE_PREFIX}-{version}.zip':
        disagree.append('archive name')
    return not disagree, 'disagrees with VERSION: ' + ', '.join(disagree)


def c_brand(g):
    missing = [p for p in BRAND_FILES if g.head_bytes(p) is None]
    if missing:
        return False, f'{len(missing)} brand file(s) missing'
    differ = sum(1 for a, b in MIRRORS if g.head_bytes(a) != g.head_bytes(b))
    return differ == 0, f'{differ} brand mirror(s) differ'


def c_disclaimer(g):
    return FULL_DISCLAIMER in g.head_text('README.md'), 'README lacks the disclaimer line'


def c_display_name(g):
    names = [json.loads(g.head_text(f'{COMP}/manifest.json'))['name'],
             json.loads(g.head_text('hacs.json'))['name']]
    return all(n == DISPLAY_NAME for n in names), 'display name differs from the packaging name'


def c_identity(g):
    email = git(g.root, 'log', '-1', '--format=%ae').strip()
    problems = []
    explicit_github_noreply = GITHUB_NOREPLY.fullmatch(email) and email == g.release_identity
    if (DEFAULT_IDENTITY.search(email) and not explicit_github_noreply) or email.lower() in g.deny_identity:
        problems.append('author is a default or machine identity')
    if g.release_identity is not None and email != g.release_identity:
        problems.append('author differs from --release-identity')
    return not problems, '; '.join(problems)


def c_todo(g):
    hits = sum(len(MARKER_RE.findall(data.decode('utf-8', 'replace')))
               for _rel, data in g.head_files() if is_text(data))
    return hits == 0, f'{hits} marker(s) in public files'


def c_review_hash(g):
    if g.review_hash is None:
        return None, 'no --review-hash given'
    target = Path(g.target())
    sha = hashlib.sha256(target.read_bytes()).hexdigest()
    problems = []
    if sha != g.review_hash.lower():
        problems.append('review hash differs from the archive')
    if sha != g.build().sha256:
        problems.append('archive is not a fresh build of HEAD')
    return not problems, '; '.join(problems)


CHECKS = [
    ('results-file', c_results_file), ('results-mode', c_results_mode), ('results-head', c_results_head),
    ('results-clean', c_results_clean), ('tree-clean', c_tree_clean), ('results-suites', c_results_suites),
    ('neutrality', c_neutrality),
    ('placeholders', c_placeholders), ('docs-lint', c_docs_lint), ('archive-build', c_archive_build),
    ('archive-reproducible', c_archive_reproducible), ('archive-scan', c_archive_scan),
    ('archive-members', c_archive_members), ('versions', c_versions), ('brand', c_brand),
    ('disclaimer', c_disclaimer), ('display-name', c_display_name), ('identity', c_identity),
    ('todo', c_todo), ('review-hash', c_review_hash),
]


def run_gate(gate, strict=False):
    results = []
    for name, check in CHECKS:
        try:
            ok, reason = check(gate)
        except Exception as exc:   # the class only: messages can carry paths
            ok, reason = False, f'check error ({type(exc).__name__})'
        results.append((name, ok, reason))
        if ok is True:
            print(f'PASS {name}')
        elif ok is False:
            print(f'FAIL {name}: {reason}')
        else:
            print(f'SKIPPED {name}: {reason}')
    failed = [name for name, ok, _ in results if ok is False]
    skipped = [name for name, ok, _ in results if ok is None]
    print(f'SUMMARY: {len(results) - len(failed) - len(skipped)} passed, {len(failed)} failed, '
          f'{len(skipped)} skipped')
    if skipped:
        print('SKIPPED: ' + ', '.join(skipped))
    if strict and skipped:
        print('FAIL strict: skipped checks are not allowed in strict mode')
    return 1 if failed or (strict and skipped) else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description='Aggregate every release check into one PASS/FAIL report.')
    ap.add_argument('--patterns', type=Path, help='private literal list, kept outside the repo')
    ap.add_argument('--archive', type=Path, help='archive to check instead of a fresh build of HEAD')
    ap.add_argument('--review-hash', help='SHA-256 of the archive that was reviewed')
    ap.add_argument('--results', type=Path, help=f'results record (default {RESULTS_REL})')
    ap.add_argument('--root', default='.')
    ap.add_argument('--release-identity', help='the git author email the release must carry')
    ap.add_argument('--deny-identity', action='append', default=[], help='extra author email to refuse')
    ap.add_argument('--strict', action='store_true', help='fail when any check is skipped')
    args = ap.parse_args(argv)
    scratch = Path(tempfile.mkdtemp(prefix='release-gate-'))
    try:
        gate = Gate(root=Path(args.root).resolve(), scratch=scratch, patterns=args.patterns,
                    archive=args.archive, review_hash=args.review_hash, results=args.results,
                    release_identity=args.release_identity,
                    deny_identity=[value.lower() for value in args.deny_identity])
        return run_gate(gate, strict=args.strict)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
