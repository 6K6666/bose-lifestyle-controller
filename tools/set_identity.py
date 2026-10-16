"""Set the repo-local git identity for a release repository (release plan, Task 7 step 3).

python3 tools/set_identity.py --repo DIR --name NAME --email EMAIL [--allow-noreply]

Writes user.name and user.email with `git config --local` only; it never touches --global or --system.
Refuses a value that is empty, has control characters or angle brackets, or is not an address shape. A
machine-default address (the same test the release gate applies) is refused unless --allow-noreply is given;
the gate still refuses it, so the flag is for a deliberate, documented case only.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from release_gate import DEFAULT_IDENTITY  # noqa: E402

NAME_MAX = 100
EMAIL_SHAPE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+')


def check_name(name: str) -> str:
    if not name.strip() or name != name.strip() or len(name) > NAME_MAX:
        raise ValueError(f'the name must be 1 to {NAME_MAX} characters, with no leading or trailing spaces')
    if any(ord(c) < 32 or ord(c) == 127 for c in name) or '<' in name or '>' in name:
        raise ValueError('the name may not contain control characters or angle brackets')
    return name


def check_email(email: str, allow_machine: bool) -> str:
    if not EMAIL_SHAPE.fullmatch(email):   # no stripping: a space or newline makes the value invalid
        raise ValueError('the email must look like an address, with no spaces or control characters')
    if DEFAULT_IDENTITY.search(email) and not allow_machine:
        raise ValueError('the email is a machine-default address; use your own, or pass --allow-noreply')
    return email


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Set the repo-local git identity of a release repository.')
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--name', required=True)
    parser.add_argument('--email', required=True)
    parser.add_argument('--allow-noreply', action='store_true', help='accept a machine-default address')
    args = parser.parse_args(argv)
    try:
        name = check_name(args.name)
        email = check_email(args.email, args.allow_noreply)
    except ValueError as error:
        print(f'set_identity: {error}', file=sys.stderr)
        return 2
    if not (args.repo / '.git').exists():
        print('set_identity: --repo must be a git repository (it has no .git)', file=sys.stderr)
        return 2
    for key, value in (('user.name', name), ('user.email', email)):
        subprocess.run(['git', '-C', str(args.repo), 'config', '--local', key, value], check=True)
    print(f'Repo-local identity set in {args.repo}: {name} <{email}>')
    return 0


if __name__ == '__main__':
    sys.exit(main())
