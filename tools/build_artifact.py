"""Render custom_components/<domain>/ plus the HA test suites into a standalone tree.

build(src, dst, domain): src is a tree (the repo, or a built artifact). Its single
component directory name is the source token (bose_lifestyle in the repo, example_domain
in a built artifact). Only that underscore token is rewritten, in contents and in path
parts; hyphen forms stay. Usage: python3 tools/build_artifact.py SRC DST DOMAIN
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from subst import subst, token_pattern  # noqa: E402

COMPONENT_ROOT = 'custom_components'
DOMAIN_RE = re.compile(r'[a-z][a-z0-9_]*')
ROOT_MODULES = ('bose_commands.py', 'bose_events.py', 'bose_ir.py', 'bose_request.py')
HA_SUITES = ('tests/test_bose_integration.py', 'tests/test_bose_transport.py', 'tests/test_bose_compat.py',
             'tests/test_bose_pairing.py', 'tests/test_bose_pairing_flow.py')
TEST_SUPPORT = ('tests/_bootstrap.py',)
TEST_DIRS = ('tests/fixtures',)
SKIP_PARTS = {'__pycache__'}


def _check_file(src, rel):
    p = src / rel
    if p.is_symlink():
        raise ValueError(f'symlink refused: {rel.as_posix()}')
    if not p.is_file():
        raise FileNotFoundError(rel.as_posix())
    if not p.resolve().is_relative_to(src.resolve()):
        raise ValueError(f'path escapes source: {rel.as_posix()}')


def _walk(src, rel_dir):
    base = src / rel_dir
    if base.is_symlink():
        raise ValueError(f'symlink refused: {rel_dir}')
    if not base.is_dir():
        raise FileNotFoundError(rel_dir)
    out = []
    for p in sorted(base.rglob('*')):
        rel = p.relative_to(src)
        if SKIP_PARTS & set(rel.parts) or p.suffix == '.pyc':
            continue
        if p.is_symlink():
            raise ValueError(f'symlink refused: {rel.as_posix()}')
        if p.is_dir():
            continue
        _check_file(src, rel)
        out.append(rel)
    return out


def _source_token(src):
    base = src / COMPONENT_ROOT
    if base.is_symlink() or not base.is_dir():
        raise ValueError('source has no custom_components directory')
    comps = [p for p in base.iterdir() if p.is_dir() or p.is_symlink()]
    if len(comps) != 1 or comps[0].is_symlink():
        raise ValueError('expected exactly one component directory under custom_components')
    return comps[0].name


BINARY_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.ico'}   # copied byte for byte


def _render(rel, raw, token, domain):
    if rel.suffix.lower() in BINARY_SUFFIXES:
        return raw
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        raise ValueError(f'not UTF-8 text: {rel.as_posix()}')
    out = subst(text, domain, token=token)
    if rel.suffix == '.json':
        json.loads(out)
    return out.encode('utf-8')


def build(src, dst, domain):
    src, dst = Path(src), Path(dst)
    if not DOMAIN_RE.fullmatch(domain):
        raise ValueError(f'invalid domain: {domain!r}')
    token = _source_token(src)
    rels = _walk(src, f'{COMPONENT_ROOT}/{token}')
    rels += [Path(m) for m in ROOT_MODULES + HA_SUITES + TEST_SUPPORT]
    for m in ROOT_MODULES + HA_SUITES + TEST_SUPPORT:
        _check_file(src, Path(m))
    for d in TEST_DIRS:
        rels += _walk(src, d)

    if domain != token:
        # Refuse if the target token is already present: the reverse build would corrupt it.
        guard = token_pattern(domain)
        for rel in rels:
            if guard.search(rel.as_posix()) or guard.search((src / rel).read_bytes().decode('utf-8', 'replace')):
                raise ValueError(f'target token {domain!r} already present in source: {rel.as_posix()}')

    if dst.resolve().is_relative_to(src.resolve()):
        raise ValueError('dst must not be inside src')
    if dst.exists() and any(dst.iterdir()):
        raise ValueError('dst exists and is not empty')

    staged = {}
    for rel in rels:
        out_rel = Path(*[subst(part, domain, token=token) for part in rel.parts])
        staged[out_rel] = _render(rel, (src / rel).read_bytes(), token, domain)
    for out_rel, data in staged.items():
        target = dst / out_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('dst')
    ap.add_argument('domain')
    args = ap.parse_args(argv)
    build(args.src, args.dst, args.domain)


if __name__ == '__main__':
    main()
