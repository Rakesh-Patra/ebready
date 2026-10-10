import json
from unittest.mock import MagicMock, patch

import pytest

from ebkit.analyzer.ai_analyzer import GoogleAIAnalyzer
from ebkit.analyzer.scanner import ScanResult


@pytest.mark.parametrize("use_sdk", [True, False])
def test_google_sdk_and_rest_return_validated_config(monkeypatch, use_sdk):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    analyzer = GoogleAIAnalyzer()
    raw = json.dumps({
        "language": "python", "package_manager": "pip", "port": 8080,
        "start_command": "python app.py", "health_check_path": "/health",
    })
    sdk = MagicMock()
    client = sdk.Client.return_value.__enter__.return_value
    client.models.generate_content.return_value.text = raw
    analyzer._genai = sdk if use_sdk else None
    with patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps({
            "candidates": [{"content": {"parts": [{"text": raw}]}}]
        }).encode()
        config = analyzer.analyze(ScanResult(language="python"))
    assert config.port == 8080
    if use_sdk:
        urlopen.assert_not_called()
        call = client.models.generate_content.call_args.kwargs
        assert call["model"] == analyzer.model
        assert call["config"]["response_mime_type"] == "application/json"
        sdk.Client.return_value.__exit__.assert_called_once()
    else:
        urlopen.assert_called_once()
