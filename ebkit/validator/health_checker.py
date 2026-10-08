"""
Framework-Agnostic Generic HTTP Health Checker for EBReady.

Validates whether any deployed HTTP application (FastAPI, Flask, Django,
Node.js, Express, Next.js, Spring Boot, Go, or custom) is reachable and responding.

Rules:
1. Framework-agnostic: Does not assume FastAPI or any specific framework.
2. Status codes 200–399 are treated as healthy.
3. 4xx/5xx responses are treated as unhealthy or degraded.
4. Connection failures, timeouts, and DNS errors are treated as unhealthy.
5. Does not require /health or /healthz; probes candidates (such as user-configured path,
   /health, /healthz, /) and considers the service healthy if any valid HTTP response
   (200-399) is received.
"""

from __future__ import annotations

import logging
import socket
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Optional
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


@dataclass
class HealthCheckResult:
    """Standardized result of an HTTP health check."""

    status: str  # "healthy" | "degraded" | "unhealthy"
    url: str
    status_code: Optional[int] = None
    response_time_ms: Optional[float] = None
    error: Optional[str] = None

    @property
    def passed(self) -> bool:
        """Convenience property for pipeline gate checks."""
        return self.status == "healthy"

    def to_dict(self) -> dict:
        """Return standardized JSON-serializable dictionary."""
        return asdict(self)


class GenericHTTPHealthChecker:
    """
    Reusable, framework-agnostic HTTP health checker.
    """

    DEFAULT_PROBE_PATHS = ["/health", "/healthz", "/"]

    def __init__(
        self,
        timeout_seconds: float = 5.0,
        allow_redirects: bool = True,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.allow_redirects = allow_redirects

    def check_url(self, url: str) -> HealthCheckResult:
        """
        Check a single HTTP/HTTPS endpoint.
        Treats HTTP 200–399 as healthy, 4xx/5xx as degraded/unhealthy,
        and connection/timeout/DNS failures as unhealthy.
        """
        start_time = time.perf_counter()
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "EBReady-HealthChecker/1.0", "Accept": "*/*"},
        )

        import ssl
        ctx = ssl._create_unverified_context()

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds, context=ctx) as resp:
                elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
                code = getattr(resp, "status", 200)
                if 200 <= code <= 399:
                    return HealthCheckResult(
                        status="healthy",
                        url=url,
                        status_code=code,
                        response_time_ms=elapsed_ms,
                        error=None,
                    )
                elif 400 <= code <= 499:
                    return HealthCheckResult(
                        status="degraded",
                        url=url,
                        status_code=code,
                        response_time_ms=elapsed_ms,
                        error=f"Client HTTP error: {code}",
                    )
                else:
                    return HealthCheckResult(
                        status="unhealthy",
                        url=url,
                        status_code=code,
                        response_time_ms=elapsed_ms,
                        error=f"Server HTTP error: {code}",
                    )
        except urllib.error.HTTPError as exc:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            code = exc.code
            if 200 <= code <= 399:
                return HealthCheckResult(
                    status="healthy",
                    url=url,
                    status_code=code,
                    response_time_ms=elapsed_ms,
                    error=None,
                )
            elif 400 <= code <= 499:
                return HealthCheckResult(
                    status="degraded",
                    url=url,
                    status_code=code,
                    response_time_ms=elapsed_ms,
                    error=f"HTTP {code} ({exc.reason})",
                )
            else:
                return HealthCheckResult(
                    status="unhealthy",
                    url=url,
                    status_code=code,
                    response_time_ms=elapsed_ms,
                    error=f"HTTP {code} ({exc.reason})",
                )
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            err_msg = str(exc.reason) if hasattr(exc, "reason") else str(exc)
            return HealthCheckResult(
                status="unhealthy",
                url=url,
                status_code=None,
                response_time_ms=elapsed_ms,
                error=f"Connection failure: {err_msg}",
            )
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return HealthCheckResult(
                status="unhealthy",
                url=url,
                status_code=None,
                response_time_ms=elapsed_ms,
                error=str(exc),
            )

    def check_service(
        self,
        base_url: str,
        preferred_path: Optional[str] = None,
        probe_paths: Optional[list[str]] = None,
    ) -> HealthCheckResult:
        """
        Check if a service is healthy.
        Probes preferred_path if given. If not provided or if it fails,
        probes fallback paths (/health, /healthz, /) without requiring
        a dedicated /health endpoint.
        """
        base_url = base_url.rstrip("/")
        paths_to_test: list[str] = []

        if preferred_path:
            norm_p = preferred_path if preferred_path.startswith("/") else f"/{preferred_path}"
            paths_to_test.append(norm_p)

        candidates = probe_paths if probe_paths is not None else self.DEFAULT_PROBE_PATHS
        for p in candidates:
            norm_p = p if p.startswith("/") else f"/{p}"
            if norm_p not in paths_to_test:
                paths_to_test.append(norm_p)

        last_result: Optional[HealthCheckResult] = None

        for path in paths_to_test:
            url = f"{base_url}{path}"
            res = self.check_url(url)
            if res.status == "healthy":
                return res
            last_result = res

        return last_result or HealthCheckResult(
            status="unhealthy",
            url=base_url,
            status_code=None,
            response_time_ms=None,
            error="No endpoints could be probed.",
        )
