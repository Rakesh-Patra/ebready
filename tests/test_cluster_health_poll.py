from unittest.mock import MagicMock

import pytest
from botocore.exceptions import SSLError, ClientError

from ebkit.commands.deploy import _poll_environment_health


def test_environment_poll_waits_for_grey_health_to_turn_green(monkeypatch):
    eb_client = MagicMock()
    environment = {
        "Status": "Ready",
        "Health": "Grey",
        "CNAME": "service.example.com",
    }
    eb_client.describe_environments.side_effect = [
        {"Environments": [environment]},
        {
            "Environments": [
                {
                    "Status": "Ready",
                    "Health": "Green",
                    "CNAME": "service.example.com",
                }
            ]
        },
    ]
    monkeypatch.setattr("ebkit.commands.deploy.time.sleep", lambda _seconds: None)

    result = _poll_environment_health(eb_client, "app", "service")

    assert result == {
        "status": "Ready",
        "health": "Green",
        "url": "https://service.example.com",
    }
    assert eb_client.describe_environments.call_count == 2


def test_environment_poll_returns_ready_degraded_health():
    eb_client = MagicMock()
    eb_client.describe_environments.return_value = {
        "Environments": [
            {
                "Status": "Ready",
                "Health": "Red",
                "CNAME": "service.example.com",
            }
        ]
    }

    result = _poll_environment_health(eb_client, "app", "service")

    assert result == {
        "status": "Ready",
        "health": "Red",
        "url": "https://service.example.com",
    }
    eb_client.describe_environments.assert_called_once()


def test_poll_recovers_from_certificate_failure_without_redeploying(monkeypatch):
    client = MagicMock()
    client.describe_environments.side_effect = [
        SSLError(endpoint_url="https://aws.example", error="untrusted issuer"),
        {"Environments": [{"Status": "Ready", "Health": "Green"}]},
    ]
    sleep = MagicMock()
    monkeypatch.setattr("ebkit.commands.deploy.time.sleep", sleep)
    assert _poll_environment_health(client, "app", "env")["status"] == "Ready"
    sleep.assert_called_once_with(15)
    assert [call[0] for call in client.method_calls] == ["describe_environments"] * 2


def test_poll_limits_persistent_certificate_failure(monkeypatch):
    client = MagicMock()
    client.describe_environments.side_effect = SSLError(
        endpoint_url="https://aws.example", error="untrusted issuer"
    )
    monkeypatch.setattr("ebkit.commands.deploy.time.sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="Check Status before redeploying"):
        _poll_environment_health(client, "app", "env")
    assert client.describe_environments.call_count == 3


def test_poll_does_not_retry_permission_errors():
    client = MagicMock()
    client.describe_environments.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Denied"}}, "DescribeEnvironments"
    )
    with pytest.raises(ClientError):
        _poll_environment_health(client, "app", "env")
    client.describe_environments.assert_called_once()
