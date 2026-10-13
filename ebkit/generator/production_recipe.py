"""Conservative, reviewed distroless recipes. Unsupported evidence falls back to gated AI."""
import json
import re
import shlex
from pathlib import Path

from ebkit.models.deployment_config import DeploymentConfig


def production_recipe(config: DeploymentConfig, root: Path | None) -> str | None:
    if not root or not root.is_dir():
        return None
    language = config.language.value
    try:
        command = shlex.split(config.start_command)
    except ValueError:
        return None
    if not command or any(token in {"&&", "||", ";", "|"} or "$" in token for token in command):
        return None
    common = '# syntax=docker/dockerfile:1\n# EBKit reviewed production recipe; build/runtime/CVEs require verification.\n'
    version = config.runtime_version or ""
    if language == "node" and (root / "package.json").is_file():
        package = json.loads((root / "package.json").read_text(encoding="utf-8"))
        if command in [["npm", "start"], ["npm", "run", "start"]]:
            start = package.get("scripts", {}).get("start")
            if not isinstance(start,str):
                return None
            command = shlex.split(start)
        major = version.split(".")[0] or "24"
        if major not in {"22", "24", "26"} or command[0] != "node" or len(command) < 2 or command[1].startswith("-") or any(token in {"&&","||",";","|"} or "$" in token for token in command):
            return None
        if command[1].endswith((".ts", ".tsx")):
            return None
        deps = bool(package.get("dependencies") or package.get("devDependencies"))
        lock = (root / "package-lock.json").is_file()
        if deps and not lock:
            return None  # Do not invent a lockfile or silently change dependencies.
        build = package.get("scripts",{}).get("build")
        if build is not None and not isinstance(build,str):
            return None
        if not build and not (root / command[1]).is_file():
            return None
        install = 'COPY package.json package-lock.json ./\nRUN npm ci\n' if lock else 'COPY package.json ./\n'
        # Ensure node_modules exists even for a dependency-free application.
        return common + f'FROM node:{major}-trixie-slim AS build\nWORKDIR /app\n{install}COPY . .\n' + ('RUN npm run build\n' if build else '') + ('RUN npm prune --omit=dev && npm cache clean --force\n' if lock else '') + f'''FROM gcr.io/distroless/nodejs{major}-debian13:nonroot AS runtime
WORKDIR /app
COPY --from=build --chown=65532:65532 /app /app
ENV NODE_ENV=production PORT={config.port} HOST=0.0.0.0
USER 65532:65532
EXPOSE {config.port}
STOPSIGNAL SIGTERM
ENTRYPOINT ["/nodejs/bin/node"]
CMD {json.dumps(command[1:])}
'''
    if language == "python" and (version.startswith("3.13") or not version):
        if command[0] in {"uvicorn", "gunicorn"}:
            command = ["-m", *command]
        elif command[0] in {"python", "python3"}:
            command = command[1:]
        else:
            return None
        dependency = config.dependency_file
        if dependency and (config.package_manager.value not in {"pip", "unknown"} or not dependency.endswith(".txt") or not (root / dependency).is_file()):
            return None
        if dependency:
            install = f'COPY {dependency} /tmp/requirements.txt\nRUN pip install --no-cache-dir --target=/opt/site-packages -r /tmp/requirements.txt\n'
        else:
            install = 'RUN mkdir -p /opt/site-packages\n'
        return common + f'''FROM python:3.13-slim-trixie AS build
WORKDIR /app
{install}COPY . .
FROM gcr.io/distroless/python3-debian13:nonroot AS runtime
WORKDIR /app
COPY --from=build --chown=65532:65532 /opt/site-packages /opt/site-packages
COPY --from=build --chown=65532:65532 /app /app
ENV PYTHONPATH=/opt/site-packages PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT={config.port} HOST=0.0.0.0
USER 65532:65532
EXPOSE {config.port}
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/bin/python3"]
CMD {json.dumps(command)}
'''
    if language == "go" and (root / "go.mod").is_file():
        # A static Go recipe cannot safely infer cgo system libraries or omitted frontend tiers.
        if any('"C"' in file.read_text(encoding="utf-8",errors="replace") for file in root.glob("*.go")):
            return None
        module = (root / "go.mod").read_text(encoding="utf-8")
        declared = re.search(r"(?m)^go\s+(\d+\.\d+)",module)
        go_version = version or (declared[1] if declared else "1.26")
        if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?",go_version):
            return None
        locks = 'go.mod go.sum' if (root / "go.sum").is_file() else 'go.mod'
        entry = Path(config.entrypoint or "main.go")
        target = './' + entry.parent.as_posix() if entry.parent.as_posix() != '.' else '.'
        return common + f'''FROM golang:{go_version}-trixie AS build
WORKDIR /src
COPY {locks} ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/server {target}
FROM gcr.io/distroless/static-debian13:nonroot AS runtime
WORKDIR /app
COPY --from=build --chown=65532:65532 /out/server /app/server
USER 65532:65532
EXPOSE {config.port}
STOPSIGNAL SIGTERM
ENTRYPOINT ["/app/server"]
'''
    if language == 'java' and config.entrypoint:
        source = root / config.entrypoint
        major = (version or '21').split('.')[0]
        if major not in {'17','21','25'} or not source.is_file() or source.parent != root or source.suffix != '.java':
            return None
        content = source.read_text(encoding='utf-8')
        if re.search(r'(?m)^\s*package\s+',content) or not re.fullmatch(r'[A-Za-z_]\w*',source.stem):
            return None
        imports = re.findall(r'(?m)^\s*import\s+(?:static\s+)?([^;]+);',content)
        if any(not name.startswith(('java.','javax.','com.sun.net.httpserver.')) for name in imports):
            return None
        return common + f'''FROM eclipse-temurin:{major}-jdk AS build
WORKDIR /src
COPY {source.name} ./
RUN javac -d /out {source.name}
FROM gcr.io/distroless/java{major}-debian13:nonroot AS runtime
WORKDIR /app
COPY --from=build --chown=65532:65532 /out /app
USER 65532:65532
EXPOSE {config.port}
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/bin/java"]
CMD ["-cp", "/app", "{source.stem}"]
'''
    return None


def main():
    import argparse
    import sys
    from ebkit.analyzer.scanner import ProjectScanner
    from ebkit.validator.production import quality_report
    parser = argparse.ArgumentParser()
    parser.add_argument('--path', required=True)
    parser.add_argument('--port', required=True, type=int)
    args = parser.parse_args()
    root = Path(args.path)
    scan = ProjectScanner(root).scan()
    text = None
    if scan.architecture != 'MULTI_TIER' and scan.detected_start_command and scan.language in {'python','node','go','java'}:
        config = DeploymentConfig(language=scan.language, runtime_version=scan.runtime_version,
                                  package_manager=scan.package_manager or 'unknown', dependency_file=scan.dependency_files[0] if scan.dependency_files else None,
                                  entrypoint=scan.entrypoint, start_command=scan.detected_start_command, port=args.port)
        text = production_recipe(config, root)
    print(json.dumps({'dockerfile':text,'quality':quality_report(text,args.port,True) if text else None}))


if __name__ == '__main__':
    main()
