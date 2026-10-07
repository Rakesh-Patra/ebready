"""
Tests for framework-agnostic generic HTTP health checking and port detection.

Covers:
1. Framework-agnostic: works with any application (FastAPI, Flask, Django, Express, Next.js, Go, etc.)
2. Status codes 200-399 are treated as healthy by default.
3. 4xx/5xx responses are treated as degraded / unhealthy.
4. Connection failures, timeouts, and DNS errors are treated as unhealthy.
5. Does NOT require /health or /healthz endpoints.
6. Optional /health or /healthz can be used if present.
7. Standardized result structure: {status, url, status_code, response_time_ms, error}.
8. Exposed port detection from Dockerfile, Docker Compose, DeploymentConfig, etc.
9. Free host port dynamic mapping (HOST_FREE_PORT -> CONTAINER_PORT).
"""

import socket
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ebkit.analyzer.scanner import (
    ProjectScanner,
    extract_docker_compose_ports,
    extract_dockerfile_ports,
)
from ebkit.models.deployment_config import DeploymentConfig
from ebkit.validator.docker_validator import DockerRuntimeValidator, RuntimeResult
from ebkit.validator.health_checker import GenericHTTPHealthChecker, HealthCheckResult


class TestFrameworkAgnosticHealthChecker:
    """Tests for GenericHTTPHealthChecker covering requirements 1, 4, 5, 6, 7, 8, 9, 10."""

    @pytest.mark.parametrize("status_code", [200, 201, 204, 301, 302, 307, 308])
    def test_status_codes_200_to_399_are_healthy(self, status_code: int):
        """Status codes 200–399 are treated as healthy by default."""
        checker = GenericHTTPHealthChecker()
        mock_resp = MagicMock()
        mock_resp.status = status_code
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = checker.check_url("http://localhost:8080/some-route")

        assert result.status == "healthy"
        assert result.status_code == status_code
        assert result.error is None
        assert result.passed is True
        assert isinstance(result.response_time_ms, float)

    @pytest.mark.parametrize("status_code", [400, 401, 403, 404])
    def test_4xx_treated_as_degraded_or_unhealthy(self, status_code: int):
        """Client errors (4xx) are treated as degraded."""
        checker = GenericHTTPHealthChecker()
        err = urllib.error.HTTPError(
            url="http://localhost:8080/api",
            code=status_code,
            msg=f"Error {status_code}",
            hdrs={},  # type: ignore
            fp=None,
        )

        with patch("urllib.request.urlopen", side_effect=err):
            result = checker.check_url("http://localhost:8080/api")

        assert result.status == "degraded"
        assert result.status_code == status_code
        assert result.error is not None
        assert result.passed is False

    @pytest.mark.parametrize("status_code", [500, 502, 503, 504])
    def test_5xx_treated_as_unhealthy(self, status_code: int):
        """Server errors (5xx) are treated as unhealthy."""
        checker = GenericHTTPHealthChecker()
        err = urllib.error.HTTPError(
            url="http://localhost:8080/api",
            code=status_code,
            msg=f"Server Error {status_code}",
            hdrs={},  # type: ignore
            fp=None,
        )

        with patch("urllib.request.urlopen", side_effect=err):
            result = checker.check_url("http://localhost:8080/api")

        assert result.status == "unhealthy"
        assert result.status_code == status_code
        assert result.error is not None
        assert result.passed is False

    def test_connection_failures_and_timeouts_are_unhealthy(self):
        """Connection failures, DNS failures, and timeouts are treated as unhealthy."""
        checker = GenericHTTPHealthChecker()

        # Connection refused / DNS error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
            result = checker.check_url("http://localhost:9999/")
            assert result.status == "unhealthy"
            assert result.status_code is None
            assert "Connection refused" in (result.error or "")

        # Socket timeout
        with patch("urllib.request.urlopen", side_effect=socket.timeout("timed out")):
            result = checker.check_url("http://localhost:9999/")
            assert result.status == "unhealthy"
            assert result.status_code is None
            assert "timed out" in (result.error or "").lower()

    def test_does_not_require_health_or_healthz_endpoint(self):
        """Applications without /health or /healthz (e.g. Next.js, Django, Go) are healthy if / responds."""
        checker = GenericHTTPHealthChecker()

        def fake_urlopen(req, *args, **kwargs):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if url.endswith("/"):
                resp = MagicMock()
                resp.status = 200
                resp.__enter__ = lambda s: s
                resp.__exit__ = MagicMock(return_value=False)
                return resp
            # /health and /healthz do not exist (404)
            raise urllib.error.HTTPError(url=url, code=404, msg="Not Found", hdrs={}, fp=None)  # type: ignore

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = checker.check_service("http://localhost:3000")

        assert res.status == "healthy"
        assert res.url == "http://localhost:3000/"
        assert res.status_code == 200

    def test_optional_health_endpoint_is_used_when_available(self):
        """If /health or /healthz exists, it can be used successfully."""
        checker = GenericHTTPHealthChecker()

        def fake_urlopen(req, *args, **kwargs):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if url.endswith("/health"):
                resp = MagicMock()
                resp.status = 200
                resp.__enter__ = lambda s: s
                resp.__exit__ = MagicMock(return_value=False)
                return resp
            raise urllib.error.HTTPError(url=url, code=404, msg="Not Found", hdrs={}, fp=None)  # type: ignore

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = checker.check_service("http://localhost:8080", preferred_path="/health")

        assert res.status == "healthy"
        assert res.url == "http://localhost:8080/health"
        assert res.status_code == 200

    def test_standardized_result_format(self):
        """Returns standard dict format: {status, url, status_code, response_time_ms, error}."""
        checker = GenericHTTPHealthChecker()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = checker.check_url("http://localhost:5000/")

        d = res.to_dict()
        assert set(d.keys()) == {"status", "url", "status_code", "response_time_ms", "error"}
        assert d["status"] == "healthy"
        assert d["status_code"] == 200
        assert d["url"] == "http://localhost:5000/"
        assert d["error"] is None
        assert isinstance(d["response_time_ms"], float)


class TestPortDetectionAndMapping:
    """Tests covering port determination and host port separation."""

    def test_extract_port_from_dockerfile_expose(self):
        df = "FROM python:3.12-slim\nEXPOSE 2023\nCMD [\"python\", \"app.py\"]\n"
        exposed, cmd_ports = extract_dockerfile_ports(df)
        assert exposed == [2023]

    def test_extract_port_from_dockerfile_cmd_flag(self):
        df = "FROM node:20-alpine\nCMD [\"node\", \"server.js\", \"--port\", \"3000\"]\n"
        exposed, cmd_ports = extract_dockerfile_ports(df)
        assert cmd_ports == [3000]

    def test_extract_port_from_docker_compose(self):
        compose_yml = """
version: '3.8'
services:
  web:
    image: my-app:latest
    ports:
      - "8000:8000"
"""
        ports = extract_docker_compose_ports(compose_yml)
        assert 8000 in ports

    def test_runtime_uses_free_host_port_mapping_to_container_port(self):
        """Runtime validator maps dynamically selected host port to container port."""
        validator = DockerRuntimeValidator(max_retries=1, retry_interval=0)

        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch.object(DockerRuntimeValidator, "find_free_host_port", return_value=49152):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stdout="cid\n", stderr="")

                    mock_resp = MagicMock()
                    mock_resp.status = 200
                    mock_resp.__enter__ = lambda s: s
                    mock_resp.__exit__ = MagicMock(return_value=False)

                    with patch("urllib.request.urlopen", return_value=mock_resp):
                        result = validator.validate("custom-app:prod", port=2023)

        assert result.host_port == 49152
        assert result.container_port == 2023
        assert result.passed is True
        assert result.status == "healthy"
        assert "49152" in (result.url or "")
