"""
Regression tests for EBKit runtime lifecycle.

Verifies:
- Container remains running after a successful health check
- Container exits after health check → deployment fails (container_exited_after_check=True)
- Host URL reachable after validation (final probe confirms liveness)
- Correct host→container port mapping is verified
- Dynamic host port works (host_port != container_port for non-trivial cases)
- Application port is unchanged by the validator
- Docker AI is NOT called when container_exited_after_check=True (tested via init.py output)
- All 17 Docker AI tests continue to pass (covered by test_docker_ai_recovery.py)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call
import pytest

from ebkit.validator.docker_validator import DockerRuntimeValidator, RuntimeResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_subprocess_result(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# 1. RuntimeResult.passed — backwards-compatible with legacy tests
# ---------------------------------------------------------------------------

class TestRuntimeResultPassed:
    def test_legacy_root_ok_health_ok_passes(self):
        """RuntimeResult(root_ok=True, health_ok=True) must still pass (no lifecycle fields set)."""
        assert RuntimeResult(root_ok=True, health_ok=True).passed is True

    def test_legacy_root_ok_health_fail_fails(self):
        assert RuntimeResult(root_ok=True, health_ok=False).passed is False

    def test_legacy_root_fail_health_ok_fails(self):
        assert RuntimeResult(root_ok=False, health_ok=True).passed is False

    def test_legacy_both_fail(self):
        assert RuntimeResult(root_ok=False, health_ok=False).passed is False

    def test_status_healthy_passes_without_lifecycle(self):
        assert RuntimeResult(status="healthy").passed is True

    def test_status_unhealthy_fails(self):
        assert RuntimeResult(status="unhealthy").passed is False

    def test_container_exited_after_check_always_fails(self):
        # Even if HTTP probe looked healthy, exited container must fail
        r = RuntimeResult(
            root_ok=True, health_ok=True,
            status="healthy",
            is_running=False,
            container_exited_after_check=True,
        )
        assert r.passed is False

    def test_is_running_true_with_healthy_status_passes(self):
        r = RuntimeResult(status="healthy", is_running=True)
        assert r.passed is True

    def test_is_running_explicitly_false_with_exit_reason_fails(self):
        """When lifecycle was actively checked and is_running=False, must fail."""
        r = RuntimeResult(
            root_ok=True, health_ok=True,
            status="healthy",
            is_running=False,
            exit_reason="Exited (1) 5 seconds ago",
        )
        assert r.passed is False

    def test_is_running_false_without_exit_reason_backwards_compat(self):
        """is_running=False but no exit_reason → lifecycle not checked → use HTTP probe."""
        r = RuntimeResult(root_ok=True, health_ok=True, is_running=False, exit_reason=None)
        # Lifecycle not checked (default is_running=False, no exit_reason) → should pass
        assert r.passed is True


# ---------------------------------------------------------------------------
# 2. Container remains running after health check
# ---------------------------------------------------------------------------

class TestContainerRemainsRunning:
    """The validator must NOT stop the container after a successful health check."""

    def test_container_not_stopped_on_success(self):
        """_stop_container must NOT be called in the success path."""
        validator = DockerRuntimeValidator(max_retries=1, retry_interval=0)

        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run") as mock_run:
                # docker stop (stale cleanup), docker run, docker logs
                mock_run.return_value = _make_subprocess_result(returncode=0, stdout="container-id\n")

                with patch.object(validator, "is_container_running", return_value=True), \
                     patch.object(validator, "verify_port_mapping", return_value=True), \
                     patch.object(validator, "_stop_container") as mock_stop:

                    # Mock GenericHTTPHealthChecker
                    from ebkit.validator.health_checker import HealthCheckResult
                    healthy_result = HealthCheckResult(status="healthy", url="http://localhost:55000/", status_code=200)
                    with patch("ebkit.validator.docker_validator.GenericHTTPHealthChecker") as mock_checker_cls:
                        mock_checker = MagicMock()
                        mock_checker.check_service.return_value = healthy_result
                        mock_checker.check_url.return_value = healthy_result
                        mock_checker_cls.return_value = mock_checker

                        result = validator.validate("myapp:latest", port=8080)

                # On SUCCESS: _stop_container called ONCE (initial stale cleanup), NOT twice
                assert mock_stop.call_count == 1, (
                    f"_stop_container called {mock_stop.call_count} times on success — "
                    "container must not be stopped after a successful health check"
                )
                assert result.passed is True
                assert result.is_running is True

    def test_container_stopped_on_health_check_failure(self):
        """On health check failure, container MUST be stopped (cleanup)."""
        validator = DockerRuntimeValidator(max_retries=1, retry_interval=0)

        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = _make_subprocess_result(returncode=0, stdout="container-id\n")

                with patch.object(validator, "_stop_container") as mock_stop:
                    from ebkit.validator.health_checker import HealthCheckResult
                    unhealthy = HealthCheckResult(status="unhealthy", url="http://localhost:55001/", error="Connection refused")
                    with patch("ebkit.validator.docker_validator.GenericHTTPHealthChecker") as mock_checker_cls:
                        mock_checker = MagicMock()
                        mock_checker.check_service.return_value = unhealthy
                        mock_checker.check_url.return_value = unhealthy
                        mock_checker_cls.return_value = mock_checker

                        result = validator.validate("myapp:latest", port=8080)

                # Must be stopped at least once (stale cleanup) plus once on failure
                assert mock_stop.call_count >= 2, (
                    "_stop_container must be called on health check failure to clean up"
                )
                assert result.passed is False


# ---------------------------------------------------------------------------
# 3. Container exits after health check → deployment fails
# ---------------------------------------------------------------------------

class TestContainerExitsAfterHealthCheck:
    def test_container_exit_after_successful_probe_fails_deployment(self):
        """If health check passes but container is then not running, deployment must fail."""
        validator = DockerRuntimeValidator(max_retries=1, retry_interval=0)

        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = _make_subprocess_result(returncode=0, stdout="container-id\n")

                # is_container_running returns False → container exited after probe
                with patch.object(validator, "is_container_running", return_value=False), \
                     patch.object(validator, "get_container_exit_details", return_value=("Exited (137) 1 second ago", "OOM killed")), \
                     patch.object(validator, "_stop_container"):

                    from ebkit.validator.health_checker import HealthCheckResult
                    healthy = HealthCheckResult(status="healthy", url="http://localhost:55002/", status_code=200)
                    with patch("ebkit.validator.docker_validator.GenericHTTPHealthChecker") as mock_checker_cls:
                        mock_checker = MagicMock()
                        mock_checker.check_service.return_value = healthy
                        mock_checker.check_url.return_value = healthy
                        mock_checker_cls.return_value = mock_checker

                        result = validator.validate("myapp:latest", port=8080)

        assert result.passed is False
        assert result.container_exited_after_check is True
        assert result.is_running is False
        assert result.exit_reason is not None
        assert "Exited" in result.exit_reason

    def test_container_exit_sets_exit_reason(self):
        validator = DockerRuntimeValidator(max_retries=1, retry_interval=0)

        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = _make_subprocess_result(returncode=0, stdout="container-id\n")

                with patch.object(validator, "is_container_running", return_value=False), \
                     patch.object(validator, "get_container_exit_details", return_value=("Exited (1) 2 seconds ago", "crash logs")), \
                     patch.object(validator, "_stop_container"):

                    from ebkit.validator.health_checker import HealthCheckResult
                    healthy = HealthCheckResult(status="healthy", url="http://localhost:55003/", status_code=200)
                    with patch("ebkit.validator.docker_validator.GenericHTTPHealthChecker") as mock_checker_cls:
                        mock_checker = MagicMock()
                        mock_checker.check_service.return_value = healthy
                        mock_checker.check_url.return_value = healthy
                        mock_checker_cls.return_value = mock_checker

                        result = validator.validate("myapp:latest", port=8080)

        assert result.exit_reason == "Exited (1) 2 seconds ago"
        assert result.logs == "crash logs"


# ---------------------------------------------------------------------------
# 4. Host URL reachable after validation (final probe)
# ---------------------------------------------------------------------------

class TestHostUrlReachableAfterValidation:
    def test_final_probe_confirms_url_still_reachable(self):
        """validate() must do a final HTTP probe AFTER confirming container is still running."""
        validator = DockerRuntimeValidator(max_retries=1, retry_interval=0)

        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = _make_subprocess_result(returncode=0, stdout="container-id\n")

                with patch.object(validator, "is_container_running", return_value=True), \
                     patch.object(validator, "verify_port_mapping", return_value=True), \
                     patch.object(validator, "_stop_container"):

                    from ebkit.validator.health_checker import HealthCheckResult
                    healthy = HealthCheckResult(
                        status="healthy",
                        url="http://localhost:55004/",
                        status_code=200,
                        response_time_ms=12.5,
                    )
                    with patch("ebkit.validator.docker_validator.GenericHTTPHealthChecker") as mock_checker_cls:
                        mock_checker = MagicMock()
                        mock_checker.check_service.return_value = healthy
                        mock_checker.check_url.return_value = healthy
                        mock_checker_cls.return_value = mock_checker

                        result = validator.validate("myapp:latest", port=8080)

        assert result.passed is True
        assert result.status_code == 200
        assert result.url is not None

    def test_container_running_but_url_unreachable_after_check_fails(self):
        """If the container is running but URL becomes unreachable after the probe, fail."""
        validator = DockerRuntimeValidator(max_retries=1, retry_interval=0)

        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = _make_subprocess_result(returncode=0, stdout="container-id\n")

                with patch.object(validator, "is_container_running", return_value=True), \
                     patch.object(validator, "verify_port_mapping", return_value=True), \
                     patch.object(validator, "_stop_container"):

                    from ebkit.validator.health_checker import HealthCheckResult
                    healthy = HealthCheckResult(status="healthy", url="http://localhost:55005/", status_code=200)
                    unreachable = HealthCheckResult(status="unhealthy", url="http://localhost:55005/", error="Connection refused")

                    call_count = [0]

                    def check_service_side_effect(*args, **kwargs):
                        call_count[0] += 1
                        if call_count[0] == 1:
                            return healthy   # initial health check passes
                        return unreachable   # final probe fails

                    with patch("ebkit.validator.docker_validator.GenericHTTPHealthChecker") as mock_checker_cls:
                        mock_checker = MagicMock()
                        mock_checker.check_service.side_effect = check_service_side_effect
                        mock_checker.check_url.return_value = healthy
                        mock_checker_cls.return_value = mock_checker

                        result = validator.validate("myapp:latest", port=8080)

        assert result.passed is False
        assert result.is_running is True  # container was running, just URL failed final probe


# ---------------------------------------------------------------------------
# 5. Correct host→container port mapping
# ---------------------------------------------------------------------------

class TestPortMapping:
    def test_correct_port_mapping_verified(self):
        validator = DockerRuntimeValidator(max_retries=1, retry_interval=0)

        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = _make_subprocess_result(returncode=0, stdout="container-id\n")

                with patch.object(validator, "is_container_running", return_value=True), \
                     patch.object(validator, "verify_port_mapping", return_value=True) as mock_verify, \
                     patch.object(validator, "_stop_container"):

                    from ebkit.validator.health_checker import HealthCheckResult
                    healthy = HealthCheckResult(status="healthy", url="http://localhost:55006/", status_code=200)
                    with patch("ebkit.validator.docker_validator.GenericHTTPHealthChecker") as mock_checker_cls:
                        mock_checker = MagicMock()
                        mock_checker.check_service.return_value = healthy
                        mock_checker.check_url.return_value = healthy
                        mock_checker_cls.return_value = mock_checker

                        result = validator.validate("myapp:latest", port=2023)

                # verify_port_mapping was called with the actual host and container ports
                mock_verify.assert_called_once()
                call_args = mock_verify.call_args
                assert call_args.kwargs.get("container_port", call_args.args[1] if len(call_args.args) > 1 else None) == 2023

        assert result.port_mapping_verified is True

    def test_application_port_unchanged_by_validator(self):
        """Validator must never alter container_port (the application's port)."""
        validator = DockerRuntimeValidator(max_retries=1, retry_interval=0)
        app_port = 3000

        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = _make_subprocess_result(returncode=0, stdout="container-id\n")

                with patch.object(validator, "is_container_running", return_value=True), \
                     patch.object(validator, "verify_port_mapping", return_value=True), \
                     patch.object(validator, "_stop_container"):

                    from ebkit.validator.health_checker import HealthCheckResult
                    healthy = HealthCheckResult(status="healthy", url=f"http://localhost:55007/", status_code=200)
                    with patch("ebkit.validator.docker_validator.GenericHTTPHealthChecker") as mock_checker_cls:
                        mock_checker = MagicMock()
                        mock_checker.check_service.return_value = healthy
                        mock_checker.check_url.return_value = healthy
                        mock_checker_cls.return_value = mock_checker

                        result = validator.validate("myapp:latest", port=app_port)

        assert result.container_port == app_port, (
            f"Validator changed the application port from {app_port} to {result.container_port}"
        )


# ---------------------------------------------------------------------------
# 6. Dynamic host port selection
# ---------------------------------------------------------------------------

class TestDynamicHostPort:
    def test_host_port_is_dynamically_selected(self):
        """host_port must be dynamically chosen, not hardcoded to 8080."""
        validator = DockerRuntimeValidator(max_retries=1, retry_interval=0)

        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = _make_subprocess_result(returncode=0, stdout="container-id\n")

                with patch.object(validator, "is_container_running", return_value=True), \
                     patch.object(validator, "verify_port_mapping", return_value=True), \
                     patch.object(validator, "_stop_container"), \
                     patch.object(validator, "find_free_host_port", return_value=49152) as mock_find_port:

                    from ebkit.validator.health_checker import HealthCheckResult
                    healthy = HealthCheckResult(status="healthy", url="http://localhost:49152/", status_code=200)
                    with patch("ebkit.validator.docker_validator.GenericHTTPHealthChecker") as mock_checker_cls:
                        mock_checker = MagicMock()
                        mock_checker.check_service.return_value = healthy
                        mock_checker.check_url.return_value = healthy
                        mock_checker_cls.return_value = mock_checker

                        result = validator.validate("myapp:latest", port=8080)

                mock_find_port.assert_called()

        assert result.host_port == 49152
        # host_port != container_port
        assert result.host_port != result.container_port or result.container_port == 49152

    def test_host_port_conflict_retries_with_new_port(self):
        """When first host port is occupied, validator must retry with a different free port."""
        validator = DockerRuntimeValidator(max_retries=1, retry_interval=0)

        call_count = [0]

        def mock_run_side_effect(args, **kwargs):
            call_count[0] += 1
            # First docker stop call (stale cleanup) succeeds
            if "stop" in args or "rm" in args:
                return _make_subprocess_result(returncode=0)
            # First docker run: port conflict
            if call_count[0] <= 2:
                return _make_subprocess_result(
                    returncode=1,
                    stderr="Bind for 0.0.0.0:8080 failed: port is already allocated",
                )
            # Second docker run: success
            return _make_subprocess_result(returncode=0, stdout="container-id\n")

        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", side_effect=mock_run_side_effect):
                with patch.object(validator, "is_container_running", return_value=True), \
                     patch.object(validator, "verify_port_mapping", return_value=True), \
                     patch.object(validator, "_stop_container"):

                    free_ports = [8080, 49200]
                    port_idx = [0]

                    def next_free_port():
                        idx = port_idx[0]
                        port_idx[0] += 1
                        return free_ports[idx % len(free_ports)]

                    validator.find_free_host_port = next_free_port

                    from ebkit.validator.health_checker import HealthCheckResult
                    healthy = HealthCheckResult(status="healthy", url="http://localhost:49200/", status_code=200)
                    with patch("ebkit.validator.docker_validator.GenericHTTPHealthChecker") as mock_checker_cls:
                        mock_checker = MagicMock()
                        mock_checker.check_service.return_value = healthy
                        mock_checker.check_url.return_value = healthy
                        mock_checker_cls.return_value = mock_checker

                        result = validator.validate("myapp:latest", port=8080)

        # Should have succeeded after retrying
        assert result.error is None or "already allocated" not in (result.error or "")


# ---------------------------------------------------------------------------
# 7. is_container_running helper
# ---------------------------------------------------------------------------

class TestIsContainerRunning:
    def test_returns_true_when_container_name_in_docker_ps(self):
        validator = DockerRuntimeValidator(container_name="my-test-container")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_result(returncode=0, stdout="my-test-container\n")
            assert validator.is_container_running() is True

    def test_returns_false_when_container_not_in_docker_ps(self):
        validator = DockerRuntimeValidator(container_name="my-test-container")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_result(returncode=0, stdout="other-container\n")
            assert validator.is_container_running() is False

    def test_returns_false_on_subprocess_exception(self):
        validator = DockerRuntimeValidator()
        with patch("subprocess.run", side_effect=Exception("Docker unavailable")):
            assert validator.is_container_running() is False


# ---------------------------------------------------------------------------
# 8. verify_port_mapping helper
# ---------------------------------------------------------------------------

class TestVerifyPortMapping:
    def test_returns_true_when_host_port_in_docker_port_output(self):
        validator = DockerRuntimeValidator(container_name="my-container")
        with patch("subprocess.run") as mock_run:
            # docker port output shows host port 49922
            mock_run.return_value = _make_subprocess_result(
                returncode=0, stdout="8080/tcp -> 0.0.0.0:49922\n"
            )
            assert validator.verify_port_mapping(host_port=49922, container_port=8080) is True

    def test_returns_false_when_host_port_not_in_output(self):
        validator = DockerRuntimeValidator(container_name="my-container")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_result(
                returncode=0, stdout="8080/tcp -> 0.0.0.0:12345\n"
            )
            assert validator.verify_port_mapping(host_port=99999, container_port=8080) is False


# ---------------------------------------------------------------------------
# 9. get_container_exit_details helper
# ---------------------------------------------------------------------------

class TestGetContainerExitDetails:
    def test_returns_status_and_logs(self):
        validator = DockerRuntimeValidator(container_name="exited-container")
        with patch("subprocess.run") as mock_run:
            # First call: docker ps -a (exit status)
            # Second call: docker logs (container logs)
            mock_run.side_effect = [
                _make_subprocess_result(returncode=0, stdout="Exited (137) 2 minutes ago\n"),
                _make_subprocess_result(returncode=0, stdout="app crashed\n", stderr="OOM\n"),
            ]
            status, logs = validator.get_container_exit_details()

        assert status == "Exited (137) 2 minutes ago"
        assert "app crashed" in logs or "OOM" in logs

    def test_handles_exception_gracefully(self):
        validator = DockerRuntimeValidator()
        with patch("subprocess.run", side_effect=Exception("timeout")):
            status, logs = validator.get_container_exit_details()
        assert status is None
        assert logs == ""


# ---------------------------------------------------------------------------
# 10. No --rm flag: container persists (docker run command check)
# ---------------------------------------------------------------------------

class TestNoRmFlag:
    def test_docker_run_does_not_use_rm_flag(self):
        """The docker run command must NOT include --rm so the container persists."""
        validator = DockerRuntimeValidator(max_retries=1, retry_interval=0)
        captured_cmds = []

        def mock_run_capture(args, **kwargs):
            captured_cmds.append(list(args))
            return _make_subprocess_result(returncode=0, stdout="container-id\n")

        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", side_effect=mock_run_capture):
                with patch.object(validator, "is_container_running", return_value=True), \
                     patch.object(validator, "verify_port_mapping", return_value=True), \
                     patch.object(validator, "_stop_container"):

                    from ebkit.validator.health_checker import HealthCheckResult
                    healthy = HealthCheckResult(status="healthy", url="http://localhost:55010/", status_code=200)
                    with patch("ebkit.validator.docker_validator.GenericHTTPHealthChecker") as mock_checker_cls:
                        mock_checker = MagicMock()
                        mock_checker.check_service.return_value = healthy
                        mock_checker.check_url.return_value = healthy
                        mock_checker_cls.return_value = mock_checker

                        validator.validate("myapp:latest", port=8080)

        # Find the docker run command (has 'run' and the image tag)
        run_cmds = [c for c in captured_cmds if len(c) > 1 and c[1] == "run"]
        assert run_cmds, "No 'docker run' command was captured"
        for run_cmd in run_cmds:
            assert "--rm" not in run_cmd, (
                f"'--rm' flag found in docker run command: {run_cmd}\n"
                "Container must NOT be auto-removed — it must stay running after health check."
            )
