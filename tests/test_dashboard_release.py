import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

spec = importlib.util.spec_from_file_location('dashboard_release', Path(__file__).parents[1] / 'scripts/release_dashboard.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.mark.parametrize('statuses', [
    ['Processing', 'Processed'],
    ['PROCESSING', 'PROCESSED'],
    ['Unprocessed', 'PROCESSING', 'PROCESSED'],
])
def test_release_waits_for_actual_service_processing_states(monkeypatch, statuses):
    monkeypatch.setattr(release.time, 'sleep', lambda _: None)
    eb = MagicMock()
    eb.describe_application_versions.side_effect = [{'ApplicationVersions': [{'Status': status}]} for status in statuses]
    release.wait_for_processed_version(eb, 'app', 'candidate')
    assert eb.describe_application_versions.call_count == len(statuses)


@pytest.mark.parametrize('status', ['Failed', 'FAILED'])
def test_failed_bundle_is_rejected(monkeypatch, status):
    monkeypatch.setattr(release.time, 'sleep', lambda _: None)
    eb = MagicMock()
    eb.describe_application_versions.return_value = {'ApplicationVersions': [{'Status': status}]}
    with pytest.raises(SystemExit, match='environment was not changed'):
        release.wait_for_processed_version(eb, 'app', 'candidate')


def test_processing_timeout_does_not_look_successful(monkeypatch):
    monkeypatch.setattr(release.time, 'sleep', lambda _: None)
    eb = MagicMock()
    eb.describe_application_versions.return_value = {'ApplicationVersions': [{'Status': 'PROCESSING'}]}
    with pytest.raises(SystemExit, match='timed out'):
        release.wait_for_processed_version(eb, 'app', 'candidate', attempts=2)
