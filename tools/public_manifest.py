"""Public release manifest: the tracked files that make up the public tree.

public_files(root) returns sorted repo-relative POSIX paths from the index (working-tree view).
head_entries(root) and public_entries(root) read HEAD instead: (path, git mode, blob sha) triples,
which is what the release builder ships. The owner-only tree (maintainer/), local reports and
backups, build output, internal design docs, private fixtures, editor and OS files, runtime state,
logs, databases and backups are never public. Symlinks and submodules anywhere in HEAD fail.
"""
import fnmatch
import subprocess
from pathlib import Path
from typing import List, Tuple

EXCLUDED_PREFIXES = ('maintainer/', 'reports/', 'backups/', 'dist/', 'docs/superpowers/', '.superpowers/', 'build/',
                     'tests/fixtures/owner_')
EXCLUDED_DIRS = {'.git', '.idea', '.vscode', '__pycache__', '.venv', 'venv'}
EXCLUDED_NAMES = (
    '.git*', '.DS_Store', 'Thumbs.db', 'desktop.ini', '*.swp', '*.swo', '*~', '*.orig', '*.bak',
    '*.pyc', '*.tmp', '*.log', '*.sqlite*', '*.db', '*.sidecar*', '*.state',
    'config.json', '*config.json', '*-state.json', '*_state.json', 'state.json',
    '*-2026*.json', '*continuation*', '*dashboard*',
)
DISPLAY_NAME = 'Bose Lifestyle Assistant'   # the one place the public display name is written
BAD_MODES = {'120000', '160000'}   # symlink, submodule


def is_excluded(rel: str) -> bool:
    if rel.startswith(EXCLUDED_PREFIXES):
        return True
    parts = rel.split('/')
    if EXCLUDED_DIRS & set(parts[:-1]):
        return True
    if rel == '.gitignore':   # the one git file that ships; it holds generic patterns only
        return False
    if len(parts) == 3 and rel.startswith('docs/evidence/') and rel.endswith('.json'):   # sanitized promotion evidence
        return False
    return any(fnmatch.fnmatchcase(parts[-1], pat) for pat in EXCLUDED_NAMES)


def tracked_files(root) -> List[str]:
    out = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'], check=True, capture_output=True).stdout
    return sorted(name for name in out.decode('utf-8').split('\0') if name)


def public_files(root) -> List[str]:
    return [rel for rel in tracked_files(Path(root)) if not is_excluded(rel)]


def head_entries(root) -> List[Tuple[str, str, str]]:
    out = subprocess.run(['git', '-C', str(root), 'ls-tree', '-r', '-z', 'HEAD'],
                         check=True, capture_output=True).stdout
    entries = []
    for record in out.split(b'\0'):
        if not record:
            continue
        meta, _, path = record.partition(b'\t')
        mode, _kind, sha = meta.decode('ascii').split()
        entries.append((path.decode('utf-8'), mode, sha))
    return sorted(entries)


def public_entries(root) -> List[Tuple[str, str, str]]:
    entries = head_entries(root)
    for rel, mode, _sha in entries:
        if mode in BAD_MODES:
            raise ValueError(f'symlink or submodule refused: {rel}')
    return [entry for entry in entries if not is_excluded(entry[0])]
