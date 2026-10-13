"""Evidence-based, source-only deployment checks and reviewable repairs.

Never import or execute application code. Runtime verification is CLI-only.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from ebkit.source_bundle import included
from ebkit.validator.build_inputs import check_build_inputs


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def analyze(root: Path, port: int = 8080) -> dict:
    root = root.resolve()
    if not root.is_dir() or not 1 <= port <= 65535:
        raise ValueError("Choose an existing project directory and a port from 1 to 65535.")
    findings, patches = [], []
    candidates = []
    complete = True
    # Prune dependencies and links before traversing; bound source analysis.
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if included((d, "file"))
                         and d not in {"dist", "build", ".next", ".cache", ".jobs", "tests", "test", "__tests__"}
                         and not d.startswith(".pytest")
                         and not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            file = Path(directory) / name
            relative = file.relative_to(root)
            if file.is_symlink() or not included(relative.parts):
                continue
            if name.startswith("test_") or re.search(r"\.(?:test|spec)\.[cm]?[jt]s$", name):
                continue
            if name not in {"Dockerfile", "package.json"} and file.suffix not in {".py", ".js", ".ts", ".mjs", ".cjs"}:
                continue
            if len(candidates) >= 250 or file.stat().st_size > 65536:
                complete = False
                continue
            candidates.append(file)
    # Always include the root Dockerfile, even if it fell beyond the source limit.
    docker = root / "Dockerfile"
    if docker.is_file() and not docker.is_symlink() and docker.stat().st_size <= 65536 and docker not in candidates:
        candidates.append(docker)
    texts = {}
    for file in candidates:
        try:
            texts[file.relative_to(root).as_posix()] = file.read_bytes().decode("utf-8")
        except UnicodeError:
            complete = False

    def finding(code, path, line, message, fixable=False):
        findings.append(dict(code=code, path=path, line=line, message=message, fixable=fixable))

    def patch(path, before, after):
        if after != before:
            patches.append(dict(path=path, before=before, after=after,
                                diff="".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                                                  fromfile=f"a/{path}", tofile=f"b/{path}", n=2))))

    df = texts.get("Dockerfile", "")
    if not df:
        finding("dockerfile-missing", "Dockerfile", 1, "Root Dockerfile missing or unreadable. Run ebkit init, review it, then rerun doctor.")
    else:
        try:
            check_build_inputs(root, df)
        except (ValueError, OSError, TypeError, AttributeError) as error:
            finding("build-inputs", "Dockerfile", 1, str(error))
        if not re.search(r"^\s*FROM\s+\S+", df, re.M | re.I):
            finding("base-image", "Dockerfile", 1, "Dockerfile needs a FROM instruction.")
        if not re.search(r"^\s*(CMD|ENTRYPOINT)\s+", df, re.M | re.I):
            finding("startup-missing", "Dockerfile", 1, "No startup command. Add CMD or ENTRYPOINT for your application.")
        vite = False
        for name, text in texts.items():
            if name.endswith("package.json"):
                try:
                    manifest = json.loads(text)
                    version = {**manifest.get("dependencies", {}), **manifest.get("devDependencies", {})}.get("vite", "")
                    match = re.match(r"[~^]?(\d+)", str(version))
                    vite |= bool(match and int(match[1]) >= 7)
                except (ValueError, AttributeError, TypeError):
                    pass  # The existing build-input validator reports malformed manifests.
        lines = df.splitlines(True)
        exposed = list(re.finditer(r"^\s*EXPOSE\s+(\d+)(?:/tcp)?\s*$", df, re.M | re.I))
        for index, line in enumerate(lines):
            updated = line
            if vite and re.match(r"^\s*FROM\s+(?:docker.io/library/)?node:(?:1\d|20\.(?:\d|1[0-8]))(?:[.\s-]|$)", line, re.I):
                fixable = "@sha256:" not in line
                finding("node-runtime", "Dockerfile", index + 1, "Vite 7+ requires a newer Node runtime; use Node 22 (22.12+).", fixable)
                if fixable:
                    updated = re.sub(r"(node:)[\d.]+", r"\g<1>22", updated, count=1)
            if len(exposed) == 1 and re.match(r"^\s*EXPOSE\s+\d+(?:/tcp)?\s*$", line, re.I):
                actual = int(re.search(r"\d+", line).group())
                if actual != port:
                    finding("exposed-port", "Dockerfile", index + 1, f"EXPOSE {actual} differs from selected container port {port}. Confirm the app listens on {port}.", True)
                    updated = re.sub(r"(EXPOSE\s+)\d+", rf"\g<1>{port}", updated, count=1, flags=re.I)
            if re.match(r"^\s*(CMD|ENTRYPOINT)\s+", line, re.I):
                # Only explicit HTTP server host flags; never replace database URLs.
                host = r'((?:--host|--bind|-b)(?:[\s"\x27,=]+))(localhost|127\.0\.0\.1)(?=[:\s"\x27,\]])'
                if re.search(host, updated) and re.search(r"\b(uvicorn|gunicorn|flask|vite|next)\b", updated):
                    finding("loopback-binding", "Dockerfile", index + 1, "Startup command binds to loopback. Container ingress requires 0.0.0.0.", True)
                    updated = re.sub(host, r"\g<1>0.0.0.0", updated)
            lines[index] = updated
        patch("Dockerfile", df, "".join(lines))

    for name, text in texts.items():
        updated = text
        if name.endswith(".py"):
            try:
                tree = ast.parse(text)
                replacements = []
                source_lines = text.splitlines(True)
                offsets = [0]
                for line in source_lines:
                    offsets.append(offsets[-1] + len(line))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                        continue
                    if not isinstance(node.func.value, ast.Name) or node.func.value.id not in {"app", "uvicorn"} or node.func.attr != "run":
                        continue
                    for keyword in node.keywords:
                        value = keyword.value
                        if keyword.arg == "host" and isinstance(value, ast.Constant) and value.value in {"localhost", "127.0.0.1"}:
                            # AST columns are UTF-8 byte offsets, not Python string offsets.
                            start = offsets[value.lineno - 1] + len(source_lines[value.lineno - 1].encode()[:value.col_offset].decode())
                            end = offsets[value.end_lineno - 1] + len(source_lines[value.end_lineno - 1].encode()[:value.end_col_offset].decode())
                            replacements.append((start, end))
                            finding("loopback-binding", name, value.lineno, "HTTP server binds to loopback. Use 0.0.0.0 for container ingress.", True)
                for start, end in sorted(replacements, reverse=True):
                    updated = updated[:start] + '"0.0.0.0"' + updated[end:]
            except SyntaxError:
                finding("python-syntax", name, 1, "Python source could not be parsed. Check its syntax.")
        elif name.endswith((".js", ".ts", ".mjs", ".cjs")):
            pattern = r'(\.listen\(\s*(?:\d+|port|PORT|process\.env\.PORT)\s*,\s*)(["\x27])(localhost|127\.0\.0\.1)\2'
            # Mask comments and literal strings/templates before matching call sites.
            ignored = list(re.finditer(r'//[^\n]*|/\*[\s\S]*?(?:\*/|$)|"(?:\\.|[^"\\])*"|\x27(?:\\.|[^\x27\\])*\x27|`(?:\\.|[^`\\])*`', text))
            replacements = []
            for match in re.finditer(pattern, text):
                line = text[:match.start()].count("\n") + 1
                if any(span.start() <= match.start() < span.end() for span in ignored):
                    continue
                finding("loopback-binding", name, line, "Server listen() binds to loopback. Use 0.0.0.0 for container ingress.", True)
                replacements.append((match.start(), match.end(), match.group(1) + match.group(2) + "0.0.0.0" + match.group(2)))
            for start, end, replacement in sorted(replacements, reverse=True):
                updated = updated[:start] + replacement + updated[end:]
        patch(name, text, updated)
    if not complete:
        finding("scan-incomplete", ".", 1, "Some files exceeded the 250-file / 64 KB-per-file analysis limits or were not UTF-8. Review them manually.")
    payload = dict(port=port, findings=findings, patches=patches, scanComplete=complete,
                   verification={"static": "failed" if findings else "passed", "build": "not_verified", "runtime": "not_verified", "security": "not_verified"})
    payload["planHash"] = digest(json.dumps(payload, sort_keys=True))
    return payload


def public_report(report: dict) -> dict:
    return {**report, "patches": [{"path": p["path"], "diff": p["diff"]} for p in report["patches"]]}


def apply_repairs(root: Path, report: dict) -> dict:
    """Reject stale plans and links, stage writes, and restore originals on failure."""
    root = root.resolve()
    current = analyze(root, report["port"])
    if current["planHash"] != report["planHash"]:
        raise ValueError("Project changed after review. Run doctor again and review the new diff.")
    staged, written = [], []
    try:
        for patch in current["patches"]:
            target = root / patch["path"]
            if target.is_symlink() or not target.resolve().is_relative_to(root):
                raise ValueError("Repair target escapes the project directory.")
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                handle.write(patch["after"].encode("utf-8"))
                temporary = Path(handle.name)
            temporary.chmod(target.stat().st_mode)
            staged.append((target, temporary, patch["before"]))
        for target, temporary, before in staged:
            os.replace(temporary, target)
            written.append((target, before))
    except Exception:
        for target, before in written:
            target.write_bytes(before.encode("utf-8"))
        raise
    finally:
        for _, temporary, _ in staged:
            temporary.unlink(missing_ok=True)
    result = public_report(analyze(root, report["port"]))
    result["applied"] = [patch["path"] for patch in current["patches"]]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--apply-hash")
    args = parser.parse_args()
    try:
        root = Path(args.path)
        report = analyze(root, args.port)
        if args.apply_hash:
            if args.apply_hash != report["planHash"]:
                raise ValueError("Project changed after review. Run doctor again.")
            result = apply_repairs(root, report)
            from ebkit.source_bundle import inspect
            result["inspection"] = inspect(root)
        else:
            result = public_report(report)
        print(json.dumps(result))
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
