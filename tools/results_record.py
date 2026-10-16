"""Results record of a test run: build/test-results.json (release plan, Task 3).

run_tests.sh clears the record first, so a failing or interrupted run never leaves a stale pass,
and writes it last, only when every suite passed. The shell passes the HEAD and the dirty state it
captured at the start of the run, plus the number of suites it planned, so the record describes the
tree that was tested. The record names its runner and carries the sha256 of run_tests.sh at that HEAD.

write is internal to run_tests.sh: it refuses to run unless BOSE_RUNNER=run_tests.sh is set by the script.
CLI: python3 -B tools/results_record.py clear --results PATH
     python3 -B tools/results_record.py write --results PATH --mode stdlib|full --started ISO
         --head SHA --dirty true|false --planned N [--suites NAME ...]   (run_tests.sh only)
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MODES = ('stdlib', 'full')
RUNNER = 'run_tests.sh'
ROOT = Path(__file__).resolve().parents[1]


def now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def runner_sha256(head):
    """sha256 of run_tests.sh as committed at head (not the working copy)."""
    blob = subprocess.run(['git', '-C', str(ROOT), 'show', f'{head}:{RUNNER}'], check=True, capture_output=True).stdout
    return hashlib.sha256(blob).hexdigest()


def write_record(path, mode, started, suites, head, dirty, *, planned, runner_sha):
    if mode not in MODES:
        raise ValueError('mode must be stdlib or full')
    record = {'head': head, 'dirty': bool(dirty), 'mode': mode, 'started': started, 'finished': now_iso(),
              'suites': [{'name': name, 'exit': 0} for name in suites],
              'runner': RUNNER, 'runner_sha256': runner_sha, 'planned': int(planned)}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return record


def clear_record(path):
    Path(path).unlink(missing_ok=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Write or clear the test results record.')
    sub = ap.add_subparsers(dest='cmd', required=True)
    clear = sub.add_parser('clear')
    clear.add_argument('--results', required=True)
    write = sub.add_parser('write')
    write.add_argument('--results', required=True)
    write.add_argument('--mode', required=True, choices=MODES)
    write.add_argument('--started', required=True)
    write.add_argument('--head', required=True)
    write.add_argument('--dirty', required=True, choices=('true', 'false'))
    write.add_argument('--planned', required=True, type=int)
    write.add_argument('--suites', nargs='*', default=[])
    args = ap.parse_args(argv)
    if args.cmd == 'clear':
        clear_record(args.results)
        return 0
    if os.environ.get('BOSE_RUNNER') != RUNNER:
        print(f'REFUSED: write runs only from {RUNNER}', file=sys.stderr)
        return 2
    write_record(args.results, args.mode, args.started, args.suites, args.head, args.dirty == 'true',
                 planned=args.planned, runner_sha=runner_sha256(args.head))
    return 0


if __name__ == '__main__':
    sys.exit(main())
