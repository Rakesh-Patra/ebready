"""Dependency-only hosted scanning; never execute source or its configuration."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def scan_source(root: Path) -> dict:
    incomplete = dict(gate_passed=False, available=False, artifact_kind='source',
        scanner='trivy', summary='Dependency scan incomplete. Security is NOT VERIFIED.',
        gate_reason='The scanner could not produce a complete dependency report. Retry preparation.', details=[])
    if not shutil.which('trivy'):
        return {**incomplete, 'gate_reason': 'The hosted Trivy scanner is not installed.'}
    try:
        # Trusted empty config and ignore file prevent uploaded .trivyignore/trivy.yaml
        # from filtering vulnerabilities or selecting plugins and output templates.
        with tempfile.TemporaryDirectory(prefix='ebkit-scanner-') as directory:
            trusted = Path(directory)
            (trusted / 'config.yaml').write_text('{}', encoding='utf-8')
            (trusted / 'ignore').write_text('', encoding='utf-8')
            env = {k: v for k, v in os.environ.items() if not k.startswith('TRIVY_')}
            env.update(GOMEMLIMIT='384MiB', GOMAXPROCS='2')
            cache = Path(os.environ.get('EBKIT_JOBS_DIR', tempfile.gettempdir())) / '.trivy-cache'
            command = ['trivy', '--config', str(trusted / 'config.yaml'), 'fs',
                '--scanners', 'vuln', '--pkg-types', 'library', '--format', 'json',
                '--list-all-pkgs', '--offline-scan', '--parallel', '1', '--no-progress', '--ignorefile', str(trusted / 'ignore'),
                '--cache-dir', str(cache), '--timeout', '180s', str(root.resolve())]
            result = subprocess.run(command, cwd=directory, env=env, capture_output=True,
                text=True, encoding='utf-8', errors='replace', timeout=195)
        if result.returncode:
            if os.environ.get('EBKIT_SCANNER_DIAGNOSTICS') == 'true':
                print(result.stderr[-4000:], file=sys.stderr)
            return incomplete
        report = json.loads(result.stdout)
        results = report.get('Results')
        if report.get('SchemaVersion') != 2 or not isinstance(results, list):
            return incomplete
        packages = sum(len(item.get('Packages') or []) for item in results)
        if not packages:
            return {**incomplete, 'gate_reason': 'No supported resolved dependencies were detected. Include a supported lockfile with resolved package versions and retry.'}
        counts = dict(CRITICAL=0, HIGH=0, MEDIUM=0, LOW=0)
        details = []
        for item in results:
            for finding in item.get('Vulnerabilities') or []:
                severity = finding.get('Severity')
                if severity not in counts:
                    return {**incomplete, 'gate_reason': 'The dependency report contains an unclassified vulnerability.'}
                counts[severity] += 1
                details.append(dict(id=finding['VulnerabilityID'], severity=severity.lower(),
                    packages=[finding['PkgName'] + '@' + finding['InstalledVersion']],
                    paths=[item['Target']], fixed_version=finding.get('FixedVersion', '')))
        passed = counts['CRITICAL'] == 0 and counts['HIGH'] <= 5
        return dict(gate_passed=passed, available=True, scanner='trivy', artifact_kind='source',
            critical_count=counts['CRITICAL'], high_count=counts['HIGH'], medium_count=counts['MEDIUM'],
            low_count=counts['LOW'], total_cves=sum(counts.values()), details=details,
            summary=f'Trivy scanned {packages} resolved source packages. Application image not built or scanned.',
            gate_reason=None if passed else 'Dependency gate requires 0 Critical and at most 5 High findings.')
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        return incomplete
