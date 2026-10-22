import json
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from ebkit.validator.hosted_source import scan_source


@pytest.mark.parametrize('severity,passed',[(None,True),('HIGH',True),('CRITICAL',False),('UNKNOWN',False)])
def test_real_report_gate_and_untrusted_config_isolation(tmp_path,severity,passed):
    (tmp_path/'trivy.yaml').write_text('ignore-unfixed: true')
    finding=dict(VulnerabilityID='CVE-test',PkgName='demo',InstalledVersion='1.0',Severity=severity)
    report=dict(SchemaVersion=2,Results=[dict(Target='package-lock.json',Packages=[dict(Name='demo')],Vulnerabilities=[finding] if severity else [])])
    def run(command,**options):
        assert '--offline-scan' in command
        assert command[-1]==str(tmp_path.resolve())
        assert options['cwd']!=str(tmp_path)
        assert 'TRIVY_IGNORE_UNFIXED' not in options['env']
        assert '--ignorefile' in command and '--config' in command
        return SimpleNamespace(returncode=0,stdout=json.dumps(report))
    with patch.dict('os.environ',{'TRIVY_IGNORE_UNFIXED':'true'}),patch('shutil.which',return_value='trivy'),patch('subprocess.run',side_effect=run):
        result=scan_source(tmp_path)
    assert result['gate_passed'] is passed
    assert result['available'] is (severity!='UNKNOWN')


@pytest.mark.parametrize('report',[{},dict(SchemaVersion=2,Results=[]),dict(SchemaVersion=2,Results=[dict(Target='package.json')])])
def test_missing_coverage_never_passes(tmp_path,report):
    with patch('shutil.which',return_value='trivy'),patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(report))):
        result=scan_source(tmp_path)
    assert result['gate_passed'] is False
    assert result['available'] is False
    assert 'critical_count' not in result


def test_scanner_error_never_reports_clean_counts(tmp_path):
    with patch('shutil.which',return_value='trivy'),patch('subprocess.run',return_value=SimpleNamespace(returncode=1,stdout='')):
        assert scan_source(tmp_path)['available'] is False


def test_six_high_findings_block(tmp_path):
    finding=dict(VulnerabilityID='CVE-test',PkgName='demo',InstalledVersion='1.0',Severity='HIGH')
    report=dict(SchemaVersion=2,Results=[dict(Target='requirements.txt',Packages=[dict(Name='demo')],Vulnerabilities=[finding]*6)])
    with patch('shutil.which',return_value='trivy'),patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(report))):
        result=scan_source(tmp_path)
    assert result['high_count']==6
    assert result['gate_passed'] is False
