"""
Docker Build and Docker Scout Security Validators.

Provides real tooling validation for generated container artifacts:
1. Docker Build verification
2. Docker Scout vulnerability scanning & security gate evaluation
3. Distinct reporting for BUILD SUCCESS vs. SECURITY VALIDATION
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class BuildResult:
    """Result of local Docker build verification."""

    success: bool
    image_tag: str
    output: str = ""
    error: Optional[str] = None
    build_time_seconds: float = 0.0


@dataclass
class ScoutResult:
    """Result of Docker Scout CVE vulnerability scan and security gate."""

    gate_passed: bool
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    total_cves: int = 0
    summary: str = ""
    details: list[dict] = field(default_factory=list)
    raw_output: str = ""
    gate_reason: Optional[str] = None
    artifact_kind: str = "image"


class DockerBuildValidator:
    """Validates that the generated Dockerfile builds cleanly."""

    def __init__(self, docker_cmd: str = "docker") -> None:
        self.docker_cmd = docker_cmd

    def is_docker_available(self) -> bool:
        """Check if docker CLI is available in PATH and daemon is responsive."""
        if not shutil.which(self.docker_cmd):
            return False
        try:
            res = subprocess.run(
                [self.docker_cmd, "info"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            return res.returncode == 0
        except Exception:
            return False

    def build(
        self,
        context_dir: Path,
        image_tag: str = "ebkit-validate:latest",
        platform: Optional[str] = None,
        timeout: int = 300,
    ) -> BuildResult:
        """
        Execute `docker build` against the directory.
        """
        if not self.is_docker_available():
            return BuildResult(
                success=False,
                image_tag=image_tag,
                error="Docker daemon is not available or not running.",
            )

        cmd = [self.docker_cmd, "build", "-t", image_tag, str(context_dir)]
        if platform:
            cmd.extend(["--platform", platform])

        logger.info("Executing Docker build: %s", " ".join(cmd))
        import time
        start_t = time.time()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            duration = time.time() - start_t
            if proc.returncode == 0:
                return BuildResult(
                    success=True,
                    image_tag=image_tag,
                    output=proc.stdout,
                    build_time_seconds=duration,
                )
            return BuildResult(
                success=False,
                image_tag=image_tag,
                output=proc.stdout,
                error=proc.stderr or proc.stdout,
                build_time_seconds=duration,
            )
        except subprocess.TimeoutExpired:
            return BuildResult(
                success=False,
                image_tag=image_tag,
                error=f"Docker build timed out after {timeout} seconds.",
            )
        except Exception as exc:
            return BuildResult(
                success=False,
                image_tag=image_tag,
                error=f"Docker build failed to start: {exc}",
            )


class DockerScoutValidator:
    """
    Evaluates image security using Docker Scout CVE scanning.
    Applies configurable security gates (e.g. 0 critical, max 5 high).
    """

    def __init__(
        self,
        docker_cmd: str = "docker",
        max_critical: int = 0,
        max_high: int = 5,
    ) -> None:
        self.docker_cmd = docker_cmd
        self.max_critical = max_critical
        self.max_high = max_high

    def is_scout_available(self) -> bool:
        """Check if Docker Scout CLI plugin is installed."""
        if not shutil.which(self.docker_cmd):
            return False
        try:
            res = subprocess.run(
                [self.docker_cmd, "scout", "version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            return res.returncode == 0
        except Exception:
            return False

    def scan_source(self, project_dir: Path, timeout: int = 180) -> ScoutResult:
        """Scan source dependencies without resolving or building an application image."""
        result = self.scan(f"fs://{project_dir.resolve()}", timeout=timeout)
        result.artifact_kind = "source"
        result.summary = f"Source dependencies: {result.summary} (application image not scanned)"
        return result

    def scan(
        self,
        image_tag: str,
        timeout: int = 180,
    ) -> ScoutResult:
        """
        Run Docker Scout scan against *image_tag* and apply the security gate.
        """
        if not self.is_scout_available():
            return ScoutResult(
                gate_passed=False,
                summary="Docker Scout plugin not available.",
                gate_reason="Docker Scout is not installed or available.",
            )

        cmd = [
            self.docker_cmd,
            "scout",
            "cves",
            "--format",
            "sarif",
            image_tag,
        ]

        logger.info("Executing Docker Scout: %s", " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            raw_text = proc.stdout or proc.stderr
            if proc.returncode != 0:
                return ScoutResult(
                    gate_passed=False,
                    summary="Docker Scout scan failed.",
                    raw_output=raw_text,
                    gate_reason=proc.stderr.strip() or f"Scout exited with code {proc.returncode}.",
                )

            critical = 0
            high = 0
            medium = 0
            low = 0
            cve_details: list[dict] = []

            # Try parsing JSON output
            try:
                data = json.loads(raw_text)
                # Parse vulnerabilities array if present
                vulnerabilities = []
                if isinstance(data, list):
                    vulnerabilities = data
                elif isinstance(data, dict):
                    vulnerabilities = data.get("vulnerabilities", [])
                    if "runs" in data:
                        for run in data["runs"]:
                            rules = run.get("tool", {}).get("driver", {}).get("rules", [])
                            for finding in run.get("results", []):
                                index = finding.get("ruleIndex")
                                if isinstance(index, int) and 0 <= index < len(rules):
                                    rule = rules[index]
                                else:
                                    rule = next(
                                        (rule for rule in rules if rule.get("id") == finding.get("ruleId")), {}
                                    )
                                properties = rule.get("properties", {})
                                severity = properties.get("cvssV3_severity", "").upper()
                                if severity not in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
                                    score = float(properties.get("security-severity", 0))
                                    if score >= 9:
                                        severity = "CRITICAL"
                                    elif score >= 7:
                                        severity = "HIGH"
                                    elif score >= 4:
                                        severity = "MEDIUM"
                                    elif score > 0:
                                        severity = "LOW"
                                    else:
                                        severity = "UNSPECIFIED"
                                vulnerabilities.append({"id": finding.get("ruleId"), "severity": severity})
                                cve_details.append({
                                    "id": finding.get("ruleId"),
                                    "severity": severity,
                                    "packages": properties.get("purls", []),
                                    "fixed_version": properties.get("fixed_version") or "not reported",
                                    "paths": [
                                        location.get("physicalLocation", {}).get("artifactLocation", {}).get("uri", "")
                                        for location in finding.get("locations", [])
                                    ],
                                    "url": rule.get("helpUri", ""),
                                })
                    elif "vulnerabilities" not in data:
                        raise ValueError("Unrecognized Scout report format")
                else:
                    raise ValueError("Unrecognized Scout report format")

                # Tally severities
                for item in vulnerabilities:
                    sev = ""
                    if isinstance(item, dict):
                        sev = (
                            item.get("severity", "")
                            or item.get("level", "")
                            or ""
                        ).upper()
                    if "CRITICAL" in sev:
                        critical += 1
                    elif "HIGH" in sev:
                        high += 1
                    elif "MEDIUM" in sev:
                        medium += 1
                    elif "LOW" in sev:
                        low += 1

            except Exception:
                # Text fallback parsing
                import re
                crit_m = re.search(r"(\d+)\s+critical", raw_text, re.IGNORECASE)
                high_m = re.search(r"(\d+)\s+high", raw_text, re.IGNORECASE)
                med_m = re.search(r"(\d+)\s+medium", raw_text, re.IGNORECASE)
                low_m = re.search(r"(\d+)\s+low", raw_text, re.IGNORECASE)

                if crit_m:
                    critical = int(crit_m.group(1))
                if high_m:
                    high = int(high_m.group(1))
                if med_m:
                    medium = int(med_m.group(1))
                if low_m:
                    low = int(low_m.group(1))
                if not any((crit_m, high_m, med_m, low_m)):
                    return ScoutResult(
                        gate_passed=False,
                        summary="Docker Scout report could not be parsed.",
                        raw_output=raw_text,
                        gate_reason="Scout returned no recognized vulnerability report.",
                    )

            total = critical + high + medium + low

            # Security gate evaluation
            gate_passed = True
            reasons = []

            if critical > self.max_critical:
                gate_passed = False
                reasons.append(
                    f"Critical CVEs ({critical}) exceed security gate threshold ({self.max_critical})."
                )
            if high > self.max_high:
                gate_passed = False
                reasons.append(
                    f"High CVEs ({high}) exceed security gate threshold ({self.max_high})."
                )

            summary_msg = (
                f"Vulnerabilities: {critical} Critical, {high} High, {medium} Medium, {low} Low "
                f"(Total: {total})"
            )

            gate_reason = "; ".join(reasons) if reasons else "Security gate passed."

            return ScoutResult(
                gate_passed=gate_passed,
                critical_count=critical,
                high_count=high,
                medium_count=medium,
                low_count=low,
                total_cves=total,
                summary=summary_msg,
                details=cve_details,
                raw_output=raw_text,
                gate_reason=gate_reason,
            )

        except subprocess.TimeoutExpired:
            return ScoutResult(
                gate_passed=False,
                summary=f"Docker Scout scan timed out after {timeout}s.",
                gate_reason="Scan timed out.",
            )
        except Exception as exc:
            return ScoutResult(
                gate_passed=False,
                summary=f"Docker Scout scan execution failed: {exc}",
                gate_reason=str(exc),
            )


from ebkit.validator.health_checker import GenericHTTPHealthChecker, HealthCheckResult


@dataclass
class RuntimeResult:
    """Result of a live container health probe."""

    root_ok: bool = False
    health_ok: bool = False
    container_name: str = "ebready-validation"
    host_port: int = 0
    container_port: int = 0
    logs: str = ""
    error: Optional[str] = None
    # Standardized generic health result fields
    status: str = "unhealthy"  # "healthy" | "degraded" | "unhealthy"
    url: str = ""
    status_code: Optional[int] = None
    response_time_ms: Optional[float] = None

    is_running: bool = False
    port_mapping_verified: bool = False
    exit_reason: Optional[str] = None
    container_exited_after_check: bool = False

    @property
    def passed(self) -> bool:
        """
        True if container is running, port mapping is verified,
        and application is healthy (HTTP 200-399 on probed endpoint).
        Must NOT have exited after health check.

        Backwards-compatible: when lifecycle fields are at default values
        (is_running=False, container_exited_after_check=False, exit_reason=None),
        the lifecycle was not actively checked, so fall back to HTTP-probe result only.
        """
        if self.container_exited_after_check:
            return False

        # Lifecycle was actively verified when is_running=True OR exit_reason is set
        lifecycle_was_checked = self.is_running is True or self.exit_reason is not None

        if lifecycle_was_checked:
            # When lifecycle is confirmed: require both container running AND status=healthy
            if not self.is_running:
                return False
            return self.status == "healthy"
        else:
            # Legacy/default path (lifecycle not checked): use HTTP probe fields only
            return self.status == "healthy" or (self.root_ok and self.health_ok)

    def to_dict(self) -> dict:
        """Return standardized JSON-serializable dictionary."""
        return {
            "status": self.status,
            "url": self.url,
            "status_code": self.status_code,
            "response_time_ms": self.response_time_ms,
            "error": self.error,
        }


class DockerRuntimeValidator:
    """
    Starts a built Docker image as a detached container, probes GET / and
    GET /health (both must return HTTP 200), then stops the container.

    Requires the ``requests`` library (standard in ebkit extras).
    """

    def __init__(
        self,
        docker_cmd: str = "docker",
        container_name: str = "ebready-validation",
        startup_timeout: int = 15,
        max_retries: int = 10,
        retry_interval: float = 1.5,
    ) -> None:
        self.docker_cmd = docker_cmd
        self.container_name = container_name
        self.startup_timeout = startup_timeout
        self.max_retries = max_retries
        self.retry_interval = retry_interval

    @staticmethod
    def find_free_host_port() -> int:
        """Find an available ephemeral port on the host machine."""
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    def _stop_container(self) -> None:
        """Best-effort container stop & remove."""
        try:
            subprocess.run(
                [self.docker_cmd, "stop", self.container_name],
                capture_output=True,
                timeout=15,
            )
            subprocess.run(
                [self.docker_cmd, "rm", "-f", self.container_name],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass

    def is_container_running(self, container_name: Optional[str] = None) -> bool:
        """Check if container is currently running via `docker ps`."""
        cname = container_name or self.container_name
        try:
            res = subprocess.run(
                [self.docker_cmd, "ps", "--filter", f"name=^/{cname}$", "--format", "{{.Names}}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            # Also check without leading slash filter if name format differs
            names = res.stdout.strip().splitlines()
            if any(n.strip() == cname for n in names):
                return True
            res2 = subprocess.run(
                [self.docker_cmd, "ps", "--filter", f"name={cname}", "--format", "{{.Names}}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            return any(n.strip() == cname for n in res2.stdout.strip().splitlines())
        except Exception:
            return False

    def get_container_logs(self, container_name: Optional[str] = None) -> str:
        """Get container stdout/stderr logs via `docker logs`."""
        cname = container_name or self.container_name
        try:
            res = subprocess.run(
                [self.docker_cmd, "logs", cname],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            return (res.stdout + res.stderr).strip()
        except Exception:
            return ""

    def get_container_exit_details(self, container_name: Optional[str] = None) -> tuple[Optional[str], str]:
        """
        Inspect stopped/exited container using `docker ps -a` and `docker logs`.
        Returns (status_string, logs).
        """
        cname = container_name or self.container_name
        status_info: Optional[str] = None
        try:
            res = subprocess.run(
                [self.docker_cmd, "ps", "-a", "--filter", f"name={cname}", "--format", "{{.Status}}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            for line in res.stdout.strip().splitlines():
                if line.strip():
                    status_info = line.strip()
                    break
        except Exception:
            pass
        logs = self.get_container_logs(cname)
        return status_info, logs

    def verify_port_mapping(self, host_port: int, container_port: int, container_name: Optional[str] = None) -> bool:
        """Verify that docker port mapping matches HOST_PORT -> CONTAINER_APPLICATION_PORT."""
        cname = container_name or self.container_name
        try:
            res = subprocess.run(
                [self.docker_cmd, "port", cname],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            out = res.stdout.strip()
            # docker port output format: 8080/tcp -> 0.0.0.0:49922 or 0.0.0.0:49922
            # or [::]:49922
            if f":{host_port}" in out:
                return True
            # Also check docker inspect NetworkSettings.Ports
            import json
            inspect_res = subprocess.run(
                [self.docker_cmd, "inspect", cname, "--format", "{{json .NetworkSettings.Ports}}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            ports_json = json.loads(inspect_res.stdout.strip())
            for c_port_key, host_bindings in ports_json.items():
                if host_bindings and any(b.get("HostPort") == str(host_port) for b in host_bindings):
                    return True
        except Exception:
            pass
        return False

    def validate(
        self,
        image_tag: str,
        port: int = 8080,
        timeout: int = 60,
        health_check_path: Optional[str] = None,
    ) -> RuntimeResult:
        """
        Run *image_tag* as a detached container mapping HOST_FREE_PORT -> CONTAINER_APPLICATION_PORT,
        perform framework-agnostic HTTP health checks, and leave the container RUNNING on success.

        The container is only stopped if the health check FAILS (cleanup on failure).
        On success the container remains running so the user can access the application.
        """
        import time

        if not shutil.which(self.docker_cmd):
            return RuntimeResult(
                root_ok=False,
                health_ok=False,
                container_name=self.container_name,
                host_port=0,
                container_port=port,
                status="unhealthy",
                error="Docker CLI not found.",
            )

        # Ensure any stale container from a previous run is gone
        self._stop_container()

        max_host_port_attempts = 5
        container_started = False
        last_start_error = ""
        host_port = 0

        for attempt in range(max_host_port_attempts):
            host_port = self.find_free_host_port()
            logger.info(
                "Selected free host port %d for container application port %d (attempt %d/%d)",
                host_port, port, attempt + 1, max_host_port_attempts,
            )

            # NOTE: No --rm flag so the container persists after the health check
            run_cmd = [
                self.docker_cmd,
                "run",
                "-d",
                "-p", f"{host_port}:{port}",
                "--name", self.container_name,
                image_tag,
            ]
            logger.info("Starting container: %s", " ".join(run_cmd))
            try:
                proc = subprocess.run(
                    run_cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                )
                if proc.returncode != 0:
                    err_msg = proc.stderr.strip() or proc.stdout.strip()
                    if "port is already allocated" in err_msg.lower() or "address already in use" in err_msg.lower():
                        logger.warning(
                            "Host port %d is already allocated; retrying with another free host port...",
                            host_port,
                        )
                        last_start_error = err_msg
                        continue
                    return RuntimeResult(
                        root_ok=False,
                        health_ok=False,
                        container_name=self.container_name,
                        host_port=host_port,
                        container_port=port,
                        status="unhealthy",
                        error=f"Container failed to start: {err_msg}",
                    )
                container_started = True
                break
            except Exception as exc:
                return RuntimeResult(
                    root_ok=False,
                    health_ok=False,
                    container_name=self.container_name,
                    host_port=host_port,
                    container_port=port,
                    status="unhealthy",
                    error=f"Container start error: {exc}",
                )

        if not container_started:
            return RuntimeResult(
                root_ok=False,
                health_ok=False,
                container_name=self.container_name,
                host_port=host_port,
                container_port=port,
                status="unhealthy",
                error=f"Container failed to start after {max_host_port_attempts} host port attempts: {last_start_error}",
            )

        base_url = f"http://localhost:{host_port}"
        root_ok = False
        health_ok = False
        logs = ""
        health_checker = GenericHTTPHealthChecker(timeout_seconds=5.0)
        final_health: Optional[HealthCheckResult] = None
        health_check_passed = False

        try:
            # Wait for container startup with retries using generic HTTP health check
            for attempt in range(self.max_retries):
                time.sleep(self.retry_interval)
                final_health = health_checker.check_service(
                    base_url=base_url,
                    preferred_path=health_check_path,
                )
                logger.debug(
                    "Health check attempt %d: status=%s, url=%s, code=%s",
                    attempt + 1, final_health.status, final_health.url, final_health.status_code,
                )
                if final_health.status == "healthy":
                    health_check_passed = True
                    break

            # Collect legacy / compatibility flags
            root_res = health_checker.check_url(f"{base_url}/")
            root_ok = root_res.status == "healthy"
            health_res = health_checker.check_url(f"{base_url}/health")
            health_ok = health_res.status == "healthy"

        except Exception as exc:
            logger.warning("Health check raised unexpected error: %s", exc)
            health_check_passed = False

        if not health_check_passed:
            # Health check failed — collect diagnostics and clean up the container
            try:
                log_proc = subprocess.run(
                    [self.docker_cmd, "logs", self.container_name],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                )
                logs = log_proc.stdout + log_proc.stderr
            except Exception:
                logs = ""
            self._stop_container()

            if final_health is None:
                final_health = HealthCheckResult(
                    status="unhealthy",
                    url=base_url,
                    error="No health check attempts were made.",
                )
            return RuntimeResult(
                root_ok=root_ok,
                health_ok=health_ok,
                container_name=self.container_name,
                host_port=host_port,
                container_port=port,
                logs=logs,
                is_running=False,
                status=final_health.status,
                url=final_health.url,
                status_code=final_health.status_code,
                response_time_ms=final_health.response_time_ms,
                error=final_health.error,
            )

        # ── Health check passed — now verify the container is STILL running ──
        still_running = self.is_container_running()
        logger.info("Container still running after health check: %s", still_running)

        if not still_running:
            # Container exited right after the health check — capture diagnostics
            exit_reason, logs = self.get_container_exit_details()
            logger.warning("Container exited after health check. Status: %s", exit_reason)
            self._stop_container()  # cleanup any remnants
            return RuntimeResult(
                root_ok=root_ok,
                health_ok=health_ok,
                container_name=self.container_name,
                host_port=host_port,
                container_port=port,
                logs=logs,
                is_running=False,
                container_exited_after_check=True,
                exit_reason=exit_reason or "Container exited unexpectedly after health check",
                status="unhealthy",
                url=f"{base_url}/",
                error=f"Container exited after health check. Last status: {exit_reason}",
            )

        # Container is still running — verify port mapping
        port_mapping_ok = self.verify_port_mapping(host_port=host_port, container_port=port)
        logger.info("Port mapping %d->%d verified: %s", host_port, port, port_mapping_ok)

        # Final reachability probe — confirm URL is still accessible right now
        final_probe = health_checker.check_service(
            base_url=base_url,
            preferred_path=health_check_path,
        )
        still_reachable = final_probe.status == "healthy"

        # Collect current logs for reference
        try:
            log_proc = subprocess.run(
                [self.docker_cmd, "logs", self.container_name],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            logs = log_proc.stdout + log_proc.stderr
        except Exception:
            logs = ""

        if not still_reachable:
            # Container running but URL not responding — possible race or config issue
            self._stop_container()
            return RuntimeResult(
                root_ok=root_ok,
                health_ok=health_ok,
                container_name=self.container_name,
                host_port=host_port,
                container_port=port,
                logs=logs,
                is_running=True,
                port_mapping_verified=port_mapping_ok,
                status="unhealthy",
                url=f"{base_url}/",
                error=f"Container is running but URL {base_url}/ is no longer reachable after health check.",
            )

        # ── Everything confirmed: container running, port mapped, URL reachable ──
        # Do NOT stop the container — leave it running for the user.
        return RuntimeResult(
            root_ok=root_ok,
            health_ok=health_ok,
            container_name=self.container_name,
            host_port=host_port,
            container_port=port,
            logs=logs,
            is_running=True,
            port_mapping_verified=port_mapping_ok,
            status=final_probe.status,
            url=final_probe.url or f"{base_url}/",
            status_code=final_probe.status_code,
            response_time_ms=final_probe.response_time_ms,
            error=None,
        )
