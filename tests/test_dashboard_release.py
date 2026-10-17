import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

spec = importlib.util.spec_from_file_location('dashboard_release', Path(__file__).parents[1] / 'scripts/release_dashboard.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.mark.parametrize('status', ['Unprocessed', 'UNPROCESSED', 'Processed', 'PROCESSED'])
def test_standard_dockerrun_release_does_not_start_source_processing(status):
    eb = MagicMock()
    eb.create_application_version.return_value = {'ApplicationVersion': {'Status': status}}
    release.create_standard_version(eb, 'app', 'release', 'private-bucket', 'releases/release.zip')
    request = eb.create_application_version.call_args.kwargs
    assert request['Process'] is False
    assert request['SourceBundle'] == {'S3Bucket': 'private-bucket', 'S3Key': 'releases/release.zip'}
    assert 'SourceBuildInformation' not in request and 'BuildConfiguration' not in request


@pytest.mark.parametrize('status', ['Failed', 'FAILED'])
def test_rejected_standard_bundle_stops_the_release(status):
    eb = MagicMock()
    eb.create_application_version.return_value = {'ApplicationVersion': {'Status': status}}
    with pytest.raises(SystemExit, match='environment was not changed'):
        release.create_standard_version(eb, 'app', 'release', 'private-bucket', 'releases/release.zip')
