"""Exercise mutations only against the recorded disposable live-test target."""
import json
import subprocess
import sys
import time
from pathlib import Path
import boto3

ROOT = Path(__file__).resolve().parents[1]
data = json.loads((ROOT / 'artifacts/aws-cli-live/target.json').read_text())
session = boto3.Session(region_name=data['region'])
assert session.client('sts').get_caller_identity()['Account'] == data['account']
eb = session.client('elasticbeanstalk')
target = ['--app', data['app'], '--environment', data['environment'], '--region', data['region']]

def wait():
    for _ in range(80):
        row = eb.describe_environments(ApplicationName=data['app'], EnvironmentNames=[data['environment']], IncludeDeleted=False)['Environments'][0]
        if row['Status'] == 'Ready' and row.get('Health') == 'Green':
            return
        print('Waiting for test target:', row['Status'], row.get('Health'), flush=True)
        time.sleep(15)
    raise RuntimeError('Test environment did not settle; remaining mutations were not run.')

def command(name, *flags):
    wait()
    result = subprocess.run([sys.executable, 'scripts/run_live_cli.py', name, *target, *flags], cwd=ROOT)
    if result.returncode:
        raise RuntimeError('Live command failed: ' + name)

def operation(action, *flags):
    wait()
    result = subprocess.run([sys.executable, 'scripts/run_live_cli.py', 'operations', action, *target, *flags], cwd=ROOT)
    if result.returncode:
        raise RuntimeError('Live operation failed: ' + action)

for _ in range(120):
    if (ROOT / 'artifacts/aws-cli-live/recovery-proof.json').exists():
        break
    print('Waiting for the independent release-recovery check to finish.', flush=True)
    time.sleep(10)
else:
    raise RuntimeError('Release-recovery check did not finish; controls were not run.')

command('config', '--env', 'EBKIT_CONTROL_TEST=verified', '--yes')
command('config')
command('options', '--file', 'artifacts/aws-cli-live/options.json', '--yes')
operation('autoscale', '--file', 'artifacts/aws-cli-live/autoscale.json', '--yes')
command('scale', '--min', '2', '--max', '2', '--yes')
command('health', '--json')
operation('metrics')
command('scale', '--min', '1', '--max', '1', '--yes')
command('config', '--unset', 'EBKIT_CONTROL_TEST', '--yes')
wait()
print('Live config, options, policy and replica-bound checks completed.', flush=True)
