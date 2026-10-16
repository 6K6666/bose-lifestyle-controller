"""Build the public release archive from HEAD, and scan it.

build_release(root, out_dir, version) stages the manifest files exactly as committed at HEAD
(git ls-tree and cat-file), so uncommitted edits never reach the archive. It writes
out_dir/bose-lifestyle-<version>/ (staging tree) and out_dir/bose-lifestyle-<version>.zip with
sorted members, a fixed timestamp, modes 0644/0755 and stored (uncompressed) entries, so the archive
hash does not depend on the zlib build. Shape findings fail the build.

scan_archive(archive, patterns_path=None) returns findings as 'member:reason' (or '#N:reason'
when the member name itself trips a pattern). It never echoes matched text.

CLI: python3 tools/build_release.py [--version X.Y.Z] --out DIR [--patterns FILE]
(--version defaults to the VERSION file, which is the single version source.)
"""
import argparse
import hashlib
import re
import stat
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from public_manifest import is_excluded, public_entries  # noqa: E402

SHAPES_FILE = TOOLS / 'neutral_shapes.txt'
ARCHIVE_DATE = (1980, 1, 1, 0, 0, 0)
ARCHIVE_PREFIX = 'bose-lifestyle'
REPO = TOOLS.parent
VERSION_RE = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+')
BINARY_SUFFIXES = ('.png', '.jpg', '.jpeg', '.gif', '.ico', '.zip', '.gz')
MODES = {'100644': 0o644, '100755': 0o755}
ALLOWED_PERMS = (0o644, 0o755)


def default_version(root=REPO) -> str:
    return (Path(root) / 'VERSION').read_text(encoding='utf-8').strip()


@dataclass
class ReleaseInfo:
    tree: Path
    archive: Path
    sha256: str
    members: List[str]


def _lines(path) -> List[str]:
    return [ln for ln in Path(path).read_text(encoding='utf-8').splitlines()
            if ln.strip() and not ln.startswith('#')]


def _head_blob(root, sha) -> bytes:
    return subprocess.run(['git', '-C', str(root), 'cat-file', 'blob', sha],
                          check=True, capture_output=True).stdout


def build_release(root, out_dir, version) -> ReleaseInfo:
    root, out_dir = Path(root), Path(out_dir)
    if not VERSION_RE.fullmatch(version):
        raise ValueError('version must look like X.Y.Z')
    name = f'{ARCHIVE_PREFIX}-{version}'
    tree, archive = out_dir / name, out_dir / f'{name}.zip'
    if tree.exists() or archive.exists():
        raise FileExistsError('release output already exists')

    staged = {}
    for rel, mode, sha in public_entries(root):
        if mode not in MODES:
            raise ValueError(f'unsupported entry mode {mode}: {rel}')
        data = _head_blob(root, sha)
        if not rel.endswith(BINARY_SUFFIXES):
            try:
                data.decode('utf-8')
            except UnicodeDecodeError:
                raise ValueError(f'not UTF-8 text: {rel}')
        staged[rel] = (MODES[mode], data)

    members = sorted(staged)
    for rel in members:
        target = tree / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(staged[rel][1])
        target.chmod(staged[rel][0])

    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as zf:
        for rel in members:
            info = zipfile.ZipInfo(rel, date_time=ARCHIVE_DATE)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = (stat.S_IFREG | staged[rel][0]) << 16
            zf.writestr(info, staged[rel][1])

    findings = scan_archive(archive)
    if findings:
        archive.unlink()
        raise ValueError(f'archive scan failed: {len(findings)} finding(s)')
    sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
    return ReleaseInfo(tree=tree, archive=archive, sha256=sha256, members=members)


def _label(name, index, patterns_hit):
    return f'#{index}' if patterns_hit else name


def scan_archive(archive, patterns_path=None) -> List[str]:
    shapes = [re.compile(s) for s in _lines(SHAPES_FILE)]
    literals = [lit.lower() for lit in _lines(patterns_path)] if patterns_path else []
    findings = []
    with zipfile.ZipFile(archive) as zf:
        if zf.comment:
            findings.append('(archive):comment not empty')
        for index, info in enumerate(zf.infolist()):
            name = info.filename
            name_hit = (any(rx.search(name) for rx in shapes)
                        or any(lit in name.lower() for lit in literals))
            label = _label(name, index, name_hit)
            if name_hit:
                findings.append(f'{label}:name matches a pattern')
            if name.startswith(('/', '..')) or '..' in name.split('/') or '\\' in name:
                findings.append(f'{label}:unsafe member name')
            if name.endswith('/'):
                findings.append(f'{label}:directory entry')
                continue
            if is_excluded(name):
                findings.append(f'{label}:excluded path')
            mode = info.external_attr >> 16
            if mode and stat.S_ISLNK(mode):
                findings.append(f'{label}:symlink')
            elif mode and not stat.S_ISREG(mode):
                findings.append(f'{label}:not a regular file')
            elif not mode or stat.S_IMODE(mode) not in ALLOWED_PERMS:
                findings.append(f'{label}:mode not 0644 or 0755')
            if info.date_time != ARCHIVE_DATE:
                findings.append(f'{label}:timestamp not normalized')
            if info.comment:
                findings.append(f'{label}:member comment present')
            if name.endswith(BINARY_SUFFIXES):
                continue
            try:
                text = zf.read(info).decode('utf-8')
            except UnicodeDecodeError:
                findings.append(f'{label}:not UTF-8 text')
                continue
            for n, line in enumerate(text.splitlines(), 1):
                for i, rx in enumerate(shapes):
                    if rx.search(line):
                        findings.append(f'{label}:line {n}: shape {i}')
                if any(lit in line.lower() for lit in literals):
                    findings.append(f'{label}:line {n}: literal')
    return findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Build and scan the public release archive from HEAD.')
    ap.add_argument('--version', default=None)
    ap.add_argument('--out', required=True)
    ap.add_argument('--patterns', help='private literal list (outside the repo); findings never echo it')
    ap.add_argument('--root', default=str(REPO))
    args = ap.parse_args(argv)
    version = args.version or default_version(args.root)
    info = build_release(Path(args.root), Path(args.out), version)
    findings = scan_archive(info.archive, Path(args.patterns) if args.patterns else None)
    print(f'archive: {info.archive}')
    print(f'sha256: {info.sha256}')
    print(f'members: {len(info.members)}')
    print(f'scan findings: {len(findings)}')
    for item in findings:
        print('  ' + item)
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
