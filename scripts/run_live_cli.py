"""Record real CLI integration checks against the isolated AWS test target."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'artifacts/aws-cli-live'
OUTPUT.mkdir(parents=True, exist_ok=True)
env = dict(os.environ)
env['EBKIT_STATE_FILE'] = str(OUTPUT / 'deployments.json')
target = json.loads((OUTPUT / 'target.json').read_text(encoding='utf-8'))
env['EBKIT_EXPECTED_AWS_ACCOUNT'] = target['account']
secrets = []
for file in (ROOT / 'web/server/.env', OUTPUT / 'application.env', OUTPUT / 'runtime.env'):
    if file.exists():
        for line in file.read_text(encoding='utf-8').splitlines():
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            if value and len(value) > 4:
                secrets.append(value)
            if key in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'EBKIT_GITHUB_TOKEN') and value:
                env[key] = value
args = sys.argv[1:]
if not args:
    raise SystemExit('Provide CLI command arguments.')
started = time.time()
confirmation = None
if args[0] == 'destroy':
    if '--environment' not in args:
        raise SystemExit('Live-test destroy requires an explicit recorded environment.')
    name = args[args.index('--environment') + 1]
    if name not in {target['environment'], target['app'] + '-copy', target.get('standardEnvironment')}:
        raise SystemExit('Refusing to destroy an environment outside this test run.')
    confirmation = name + '\n'
result = subprocess.run([sys.executable, '-m', 'ebkit.cli', *args], cwd=ROOT,
                        env=env, capture_output=True, text=True, encoding='utf-8',
                        errors='replace', timeout=3600, input=confirmation)
output = result.stdout + result.stderr
for secret in sorted(set(secrets), key=len, reverse=True):
    output = output.replace(secret, '[REDACTED]')
stamp = str(time.time_ns())
(OUTPUT / (stamp + '.log')).write_text(output, encoding='utf-8')
path = OUTPUT / 'results.json'
results = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
results.append({'arguments': args, 'exitCode': result.returncode,
                'durationSeconds': round(time.time() - started, 2), 'log': stamp + '.log'})
path.write_text(json.dumps(results, indent=2), encoding='utf-8')
print(output[-14000:], flush=True)
print('Recorded live CLI exit code:', result.returncode, flush=True)
raise SystemExit(result.returncode)
