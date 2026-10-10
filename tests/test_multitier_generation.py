from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ebkit.analyzer.scanner import ProjectScanner
from ebkit.generator.docker_ai import DockerAIService
from ebkit.models.deployment_config import DeploymentConfig


def _multitier_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "frontend").mkdir(parents=True)
    (project / "backend").mkdir()
    (project / "frontend" / "package.json").write_text(
        '{"scripts":{"build":"vite build"},"dependencies":{"vite":"5.4.0"}}',
        encoding="utf-8",
    )
    (project / "backend" / "go.mod").write_text(
        "module example.com/app\ngo 1.22\n", encoding="utf-8"
    )
    (project / "backend" / "main.go").write_text(
        'package main\n'
        'func main() {\n'
        '\tif os.Getenv("POSTGRES_URL") == "" { log.Fatal("missing database") }\n'
        '\tfrontendDir := os.Getenv("FRONTEND_DIR")\n'
        '\tport := env("PORT", "8080")\n'
        '\tr.NoRoute(serveFrontend(frontendDir))\n'
        '\t_ = port\n'
        '}\n',
        encoding="utf-8",
    )
    (project / "docker-compose.yml").write_text(
        "services:\n"
        "  postgres:\n"
        "    image: postgres:16-alpine\n"
        "    ports:\n"
        '      - "5432:5432"\n'
        "  backend:\n"
        "    build: ./backend\n"
        "    environment:\n"
        "      POSTGRES_URL: postgres://db:compose-secret@example.test/app\n"
        "    ports:\n"
        '      - "8081:8080"\n'
        "  frontend:\n"
        "    build: ./frontend\n"
        "    ports:\n"
        '      - "${FRONTEND_HOST_PORT:-8080}:4173"\n',
        encoding="utf-8",
    )
    (project / ".env").write_text(
        "POSTGRES_URL=postgres://db:real-secret@example.test/app\n",
        encoding="utf-8",
    )
    return project


def test_scanner_prefers_frontend_port_and_discovers_managed_service_keys(
    tmp_path: Path,
):
    project = _multitier_project(tmp_path)

    scan = ProjectScanner(project).scan()

    assert scan.architecture == "MULTI_TIER"
    assert scan.language == "go"
    assert scan.dependency_files == ["backend/go.mod"]
    assert scan.entrypoint == "backend/main.go"
    assert scan.detected_port == 8080
    assert scan.stateful_services == ["postgres"]
    assert {"POSTGRES_URL", "FRONTEND_HOST_PORT"} <= set(scan.detected_env_vars)
    assert "real-secret" not in json.dumps(scan.as_dict())


@pytest.mark.parametrize("gordon_available", [False, True])
def test_gemini_multitier_dockerfile_generation_uses_redacted_manifests(
    tmp_path: Path, monkeypatch, gordon_available: bool
):
    project = _multitier_project(tmp_path)
    scan = ProjectScanner(project).scan()
    config = DeploymentConfig.model_validate(
        {
            "language": "go",
            "framework": None,
            "runtime_version": "1.22",
            "package_manager": "unknown",
            "dependency_file": "backend/go.mod",
            "entrypoint": "backend/main.go",
            "port": 4173,
            "start_command": "./devboard",
            "health_check_path": "/health",
            "platform": "linux/amd64",
            "architecture": "amd64",
            "container_strategy": "multi_stage",
            "environment_variables": ["POSTGRES_URL"],
            "uncertainties": [],
        }
    )
    response_body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": (
                                "```dockerfile\n"
                                "FROM node:22-alpine AS frontend-build\n"
                                "WORKDIR /src/frontend\n"
                                "COPY frontend/package.json frontend/package-lock.json ./\n"
                                "RUN npm ci --legacy-peer-deps\n"
                                "COPY frontend/ ./\n"
                                "RUN npm run build\n"
                                "FROM golang:1.22-alpine AS backend-build\n"
                                "WORKDIR /src/backend\n"
                                "COPY backend/go.mod backend/go.sum ./\n"
                                "RUN go mod download\n"
                                "COPY backend/ ./\n"
                                "RUN go test ./... && mkdir -p /out && "
                                "CGO_ENABLED=0 GOOS=linux go build -o /out/devboard .\n"
                                "FROM alpine:3.21\n"
                                "RUN apk add --no-cache ca-certificates && "
                                "addgroup -S app && adduser -S -G app app\n"
                                "COPY --from=backend-build /out/devboard "
                                "/usr/local/bin/devboard\n"
                                "COPY --from=frontend-build /src/frontend/dist /app/frontend\n"
                                "ENV FRONTEND_DIR=/app/frontend\n"
                                "EXPOSE 4173\n"
                                "USER app\n"
                                'ENTRYPOINT ["/usr/local/bin/devboard"]\n'
                                "```"
                            )
                        }
                    ]
                }
            }
        ]
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(response_body).encode("utf-8")

    monkeypatch.setenv("GEMINI_API_KEY", "test-api-key")
    monkeypatch.setattr(
        DockerAIService,
        "is_available",
        lambda _self: gordon_available,
    )
    monkeypatch.setattr("ebkit.generator.docker_ai.time.sleep", lambda *_args: None)
    captured_requests = []

    def fake_urlopen(request, timeout):
        captured_requests.append(request)
        assert timeout == 60
        return FakeResponse()

    with patch(
        "ebkit.generator.docker_ai.urllib.request.urlopen",
        fake_urlopen,
    ):
        with patch.object(
            DockerAIService,
            "ask_gordon",
            side_effect=RuntimeError("mock Gordon failure"),
        ):
            dockerfile, message = DockerAIService().generate_dockerfile(
                config,
                scan=scan,
                working_dir=project,
            )

    assert dockerfile is not None
    assert "EXPOSE 4173" in dockerfile
    assert "generated by gemini" in message.lower()
    request_content = captured_requests[0].data.decode("utf-8")
    assert "docker-compose.yml" in request_content
    assert "frontend/package.json" in request_content
    assert "backend/go.mod" in request_content
    assert "real-secret" not in request_content
    assert "compose-secret" not in request_content
    assert "POSTGRES_URL=postgres://db" not in request_content


def test_integrated_go_frontend_dockerfile_guard_accepts_complete_image(
    tmp_path: Path,
):
    project = _multitier_project(tmp_path)
    scan = ProjectScanner(project).scan()
    config = DeploymentConfig.model_validate(
        {
            "language": "go",
            "package_manager": "unknown",
            "dependency_file": "backend/go.mod",
            "entrypoint": "backend/main.go",
            "port": scan.detected_port,
            "start_command": "./devboard",
            "health_check_path": "/health",
            "platform": "linux/amd64",
            "architecture": "amd64",
            "container_strategy": "multi_stage",
        }
    )
    dockerfile = (
        "FROM node:22-alpine AS frontend-build\n"
        "WORKDIR /src/frontend\n"
        "COPY frontend/package.json frontend/package-lock.json ./\n"
        "RUN npm ci --legacy-peer-deps\n"
        "COPY frontend/ ./\n"
        "RUN npm run build\n"
        "FROM golang:1.22-alpine AS backend-build\n"
        "WORKDIR /src/backend\n"
        "COPY backend/go.mod backend/go.sum ./\n"
        "RUN go mod download\n"
        "COPY backend/ ./\n"
        "RUN go test ./... && CGO_ENABLED=0 GOOS=linux go build -o /out/devboard .\n"
        "FROM alpine:3.21\n"
        "RUN apk add --no-cache ca-certificates && addgroup -S app "
        "&& adduser -S -G app app\n"
        "COPY --from=backend-build /out/devboard /usr/local/bin/devboard\n"
        "COPY --from=frontend-build /src/frontend/dist /app/frontend\n"
        "ENV FRONTEND_DIR=/app/frontend\n"
        f"EXPOSE {config.port}\n"
        "USER app\n"
        'ENTRYPOINT ["/usr/local/bin/devboard"]\n'
    )

    assert DockerAIService._multitier_dockerfile_error(
        dockerfile,
        config,
        scan,
        project,
    ) is None


def test_multitier_dockerfile_validation_rejects_missing_start_command():
    config = DeploymentConfig.model_validate(
        {
            "language": "go",
            "package_manager": "unknown",
            "dependency_file": "go.mod",
            "entrypoint": "main.go",
            "port": 8080,
            "start_command": "./app",
            "health_check_path": "/health",
            "platform": "linux/amd64",
            "architecture": "amd64",
            "container_strategy": "multi_stage",
        }
    )
    dockerfile = (
        "FROM alpine:3.21\n"
        "COPY app /app\n"
        "EXPOSE 8080\n"
        "USER app\n"
    )

    error = DockerAIService._multitier_dockerfile_error(dockerfile, config)

    assert error == "Generated Dockerfile does not define a container startup command."


def test_integrated_go_frontend_dockerfile_guard_rejects_incomplete_ai_output(
    tmp_path: Path,
):
    project = _multitier_project(tmp_path)
    scan = ProjectScanner(project).scan()
    config = DeploymentConfig.model_validate(
        {
            "language": "go",
            "package_manager": "unknown",
            "dependency_file": "backend/go.mod",
            "entrypoint": "backend/main.go",
            "port": scan.detected_port,
            "start_command": "./devboard",
            "health_check_path": "/health",
            "platform": "linux/amd64",
            "architecture": "amd64",
            "container_strategy": "multi_stage",
        }
    )
    bad_dockerfile = (
        "FROM golang:1.22-alpine AS backend\n"
        "WORKDIR /app\n"
        "COPY backend/go.mod backend/go.sum ./\n"
        "RUN go mod download\n"
        "COPY backend/ ./\n"
        "RUN go build -o /out/devboard .\n"
        "FROM nginx:stable-alpine\n"
        "COPY --from=backend /out/devboard /usr/local/bin/devboard\n"
        "EXPOSE 8080\n"
        "USER app\n"
        'ENTRYPOINT ["/usr/local/bin/devboard"]\n'
    )

    error = DockerAIService._multitier_dockerfile_error(
        bad_dockerfile,
        config,
        scan,
        project,
    )

    assert error == "Dockerfile does not copy the frontend package manifest."
