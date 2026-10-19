"""Reject staged private configuration; scan file contents separately with Gitleaks."""
from pathlib import PurePosixPath
import subprocess
import sys


def sensitive_path(name):
    path = PurePosixPath(name.lower())
    leaf = path.name
    if leaf in {'.env.example', '.env.sample', '.env.template'} or leaf.endswith('.tfvars.example'):
        return False
    return (
        leaf == '.env' or leaf.startswith('.env.')
        or leaf in {'id_rsa', 'id_ed25519', 'credentials.json', 'credentials'}
        or leaf.endswith(('.pem', '.key', '.p12', '.pfx', '.tfplan', '.tfvars', '.tfvars.json'))
        or '.tfstate' in leaf or '.terraform' in path.parts or '.aws' in path.parts
    )


def main():
    result = subprocess.run(['git', 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z'],
                            check=True, capture_output=True)
    names = result.stdout.decode('utf-8').split('\0')
    blocked = [name for name in names if name and sensitive_path(name)]
    if blocked:
        print('Commit blocked: private configuration paths are staged:', file=sys.stderr)
        for name in blocked:
            print('  ' + name, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
