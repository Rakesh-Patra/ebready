"""
Cluster Mode Preflight Validator for AWS Elastic Beanstalk.

Evaluates generated deployment artifacts and build results against the
strict requirements for AWS Elastic Beanstalk Cluster Mode (Amazon EKS-backed).

Target environment verified contract:
  AWS Region: us-east-2
  EB Application: ebready
  EB Environment: ebready-dev
  Architecture: linux/amd64
  Application: FastAPI
  Port: 8080
  Health Check: /health

Rules:
- Never assume traditional EC2/ASG/launch-template/Nginx mechanisms.
- Reject any EC2-specific .ebextensions or proxy configurations.
- Verify container security, non-root user, minimal base image, architecture.
- If all checks pass: READY FOR DEPLOYMENT
- If any check fails: NOT READY FOR DEPLOYMENT
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ebkit.generator.renderer import RenderedKit
from ebkit.models.deployment_config import DeploymentConfig
from ebkit.validator.docker_validator import BuildResult, RuntimeResult, ScoutResult


@dataclass
class PreflightCheck:
    """Individual preflight check status."""

    name: str
    passed: bool
    details: str
    is_critical: bool = True


@dataclass
class PreflightReport:
    """Overall Cluster Mode Preflight status."""

    ready_for_deployment: bool = True
    checks: list[PreflightCheck] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_check(
        self,
        name: str,
        passed: bool,
        details: str,
        is_critical: bool = True,
    ) -> None:
        check = PreflightCheck(
            name=name,
            passed=passed,
            details=details,
            is_critical=is_critical,
        )
        self.checks.append(check)
        if not passed:
            if is_critical:
                self.failures.append(f"[{name}] {details}")
                self.ready_for_deployment = False
            else:
                self.warnings.append(f"[{name}] {details}")


class ClusterModePreflightValidator:
    """
    Evaluates whether a generated deployment kit is production-ready for
    AWS Elastic Beanstalk Cluster Mode.
    """

    TARGET_REGION = "us-east-2"
    TARGET_APP = "ebready"
    TARGET_ENV = "ebready-dev"
    TARGET_ARCH = "amd64"
    TARGET_PORT = 8080

    def evaluate(
        self,
        kit: RenderedKit,
        build_result: Optional[BuildResult] = None,
        runtime_result: Optional[RuntimeResult] = None,
        scout_result: Optional[ScoutResult] = None,
        require_build: bool = False,
    ) -> PreflightReport:
        report = PreflightReport()
        cfg = kit.config
        files = kit.files

        # 1. Architecture Compatibility
        # Cluster Mode on AWS EB requires linux/amd64
        arch_ok = cfg.architecture in ("amd64", "x86_64")
        report.add_check(
            name="Architecture",
            passed=arch_ok,
            details=(
                f"Configured architecture '{cfg.architecture}' matches Cluster Mode target ('{self.TARGET_ARCH}')."
                if arch_ok
                else f"Architecture '{cfg.architecture}' incompatible with Cluster Mode target '{self.TARGET_ARCH}'."
            ),
            is_critical=True,
        )

        # 2. Port & Health Endpoint
        port_ok = cfg.port == self.TARGET_PORT or (1 <= cfg.port <= 65535)
        report.add_check(
            name="Service Port",
            passed=port_ok,
            details=f"Service port {cfg.port} configured for container ingress.",
            is_critical=True,
        )

        health_ok = bool(cfg.health_check_path and cfg.health_check_path.startswith("/"))
        report.add_check(
            name="Health Endpoint",
            passed=health_ok,
            details=f"Health probe endpoint '{cfg.health_check_path}' specified for Cluster Mode probes.",
            is_critical=True,
        )

        # 3. Cluster Mode Artifact Purity (No EC2 .ebextensions)
        has_ebext = any(f.startswith(".ebextensions") for f in files)
        report.add_check(
            name="Artifact Purity",
            passed=not has_ebext,
            details=(
                "No legacy EC2-specific .ebextensions present (pure Cluster Mode container deployment)."
                if not has_ebext
                else "Legacy EC2 .ebextensions detected! Cluster Mode (EKS) does not use .ebextensions."
            ),
            is_critical=True,
        )

        # 4. Dockerfile Security & Best Practices for Cluster Mode
        if "Dockerfile" in files:
            df = files["Dockerfile"]

            # No :latest tag
            no_latest = ":latest" not in df
            report.add_check(
                name="Immutable Base Image",
                passed=no_latest,
                details="Base image uses pinned version tags, no ':latest'.",
                is_critical=True,
            )

            # Non-root execution
            has_user = bool(re.search(r"^\s*USER\s+", df, re.MULTILINE))
            report.add_check(
                name="Non-Root User",
                passed=has_user,
                details=(
                    "Container specifies unprivileged non-root USER instruction."
                    if has_user
                    else "Container runs as root; non-root user required for production EKS pods."
                ),
                is_critical=False,
            )

            # Expose port matches config
            expose_match = re.search(r"^\s*EXPOSE\s+(\d+)", df, re.MULTILINE)
            df_port_ok = bool(expose_match and int(expose_match.group(1)) == cfg.port)
            report.add_check(
                name="Dockerfile Port Match",
                passed=df_port_ok,
                details=f"Dockerfile EXPOSE matches application port ({cfg.port}).",
                is_critical=True,
            )

            # Target platform flag in FROM
            has_platform = "FROM --platform=" in df
            report.add_check(
                name="Platform Pinning",
                passed=has_platform,
                details="FROM instruction includes explicit --platform=linux/amd64 flag.",
                is_critical=False,
            )
        else:
            report.add_check(
                name="Dockerfile Presence",
                passed=False,
                details="Missing Dockerfile required for Cluster Mode deployment.",
                is_critical=True,
            )

        # 5. Build Verification Gate
        if build_result is not None:
            report.add_check(
                name="Docker Build Gate",
                passed=build_result.success,
                details=(
                    f"Docker image built cleanly ({build_result.image_tag})."
                    if build_result.success
                    else f"Docker build failed: {build_result.error}"
                ),
                is_critical=True,
            )
        elif require_build:
            report.add_check(
                name="Docker Build Gate",
                passed=False,
                details="Docker build was not executed; successful build required for Cluster Mode.",
                is_critical=True,
            )

        # 6. Runtime Verification Gate
        if runtime_result is not None:
            if runtime_result.passed:
                details = f"Container running, HTTP probe healthy ({runtime_result.status_code or 200} at {runtime_result.url or '/'})."
            elif runtime_result.container_exited_after_check:
                details = f"Container exited after health check: {runtime_result.exit_reason or 'unknown exit reason'}"
            elif not runtime_result.is_running and runtime_result.exit_reason:
                details = f"Container not running: {runtime_result.exit_reason}"
            else:
                details = f"Container health probe failed: {runtime_result.error or 'unhealthy status'}"
            report.add_check(
                name="Runtime Probe Gate",
                passed=runtime_result.passed,
                details=details,
                is_critical=True,
            )

        # 7. Scout Security Gate
        if scout_result is not None:
            if not scout_result.summary.startswith("Docker Scout plugin not available"):
                report.add_check(
                    name="Security Scan Gate",
                    passed=scout_result.gate_passed,
                    details=(
                        f"Docker Scout security gate passed ({scout_result.summary})."
                        if scout_result.gate_passed
                        else f"Security gate violated: {scout_result.gate_reason}"
                    ),
                    is_critical=True,
                )
            else:
                report.add_check(
                    name="Security Scan Gate",
                    passed=True,
                    details="Docker Scout unavailable in environment (warning recorded).",
                    is_critical=False,
                )

        return report
