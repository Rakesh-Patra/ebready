from unittest.mock import MagicMock

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
        "url": "http://service.example.com",
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
        "url": "http://service.example.com",
    }
    eb_client.describe_environments.assert_called_once()
