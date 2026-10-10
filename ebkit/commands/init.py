"""
ebkit init — interactive deployment kit generator with full artifact validation.

Pipeline:
  1. Interactive UX Wizard (Project location, AI provider, Saved config)
  2. Repository Scanner
  3. AI Artifact & Deployment Analysis (Gemini)
  4. Strict Pydantic DeploymentConfig & ArtifactPlan
  5. Jinja2 Templates Rendering & Docker AI (Gordon) Dockerfile Generation
  6. Cross-File Artifact Consistency Validation
  7. Safe File Writing
  8. Real Tooling Validation (Docker build & Runtime & Docker Scout)
     with Docker AI (Gordon) Error Diagnosis & Recovery Loop
  9. Cluster Mode Preflight Validation
  10. Clean Status Summary
"""

from __future__ import annotations

import logging
import os
import shutil
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

# pyrefly: ignore [missing-import]
import click

from ebkit.analyzer.ai_analyzer import get_analyzer
from ebkit.analyzer.scanner import ProjectScanner, ScanResult
from ebkit.config import EBKitConfig, load_config, save_config
from ebkit.generator.docker_ai import DockerAIService
from ebkit.generator.renderer import DeploymentKitRenderer, RenderedKit
from ebkit.models.deployment_config import DeploymentConfig
from ebkit.repo_handler import safe_clone_repo, validate_github_url
from ebkit.validator.cluster_preflight import (
    ClusterModePreflightValidator,
    PreflightReport,
)
from ebkit.validator.cross_validator import CrossFileValidator, ValidationReport
from ebkit.validator.docker_validator import (
    BuildResult,
    DockerBuildValidator,
    DockerRuntimeValidator,
    DockerScoutValidator,
    RuntimeResult,
    ScoutResult,
)

logger = logging.getLogger(__name__)

# Files that must NEVER be silently overwritten
_PROTECTED_FILES = {
    "Dockerfile",
    ".dockerignore",
    "Procfile",
    ".ebignore",
    ".env",
    ".env.example",
}


# ---------------------------------------------------------------------------
# Safety: file write with overwrite protection
# ---------------------------------------------------------------------------


def _safe_write(
    dest_dir: Path,
    rel_path: str,
    content: str,
    force: bool = False,
) -> tuple[bool, str]:
    """
    Write *content* to *dest_dir / rel_path*.

    Returns (written: bool, reason: str).
    If the file exists and is protected, prompts the user (unless --force).
    """
    target = dest_dir / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and rel_path in _PROTECTED_FILES:
        if force:
            status = "overwritten (--force)"
        else:
            click.echo(f"\n[!] WARNING: {rel_path} already exists.")
            overwrite = click.confirm(f"    Overwrite {rel_path}?", default=False)
            if not overwrite:
                return False, "skipped (existing file, user declined overwrite)"
            status = "overwritten (user confirmed)"
    else:
        status = "created"

    target.write_text(content, encoding="utf-8")
    return True, status


def _prepare_stateful_service_env(
    repo_path: Path,
    environment_variables: list[str],
    port: int,
) -> None:
    """Create blank, gitignored connection settings without replacing local secrets."""
    gitignore = repo_path / ".gitignore"
    try:
        gitignore_content = (
            gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
        )
        ignored_entries = {
            line.strip().lstrip("/") for line in gitignore_content.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        if ".env" not in ignored_entries:
            separator = "" if not gitignore_content or gitignore_content.endswith("\n") else "\n"
            gitignore.write_text(
                f"{gitignore_content}{separator}.env\n",
                encoding="utf-8",
            )

        env_file = repo_path / ".env"
        if env_file.exists():
            content = env_file.read_text(encoding="utf-8")
        else:
            content = (
                "# Local deployment settings. Fill in external service connection values.\n"
            )
        existing_keys = {
            line.partition("=")[0].strip()
            for line in content.splitlines()
            if "=" in line and not line.lstrip().startswith("#")
        }
        missing_settings = [
            f"{key}=" for key in sorted(set(environment_variables) - existing_keys)
        ]
        if "PORT" not in existing_keys:
            missing_settings.append(f"PORT={port}")
        if missing_settings:
            separator = "" if not content or content.endswith("\n") else "\n"
            appended_settings = "\n".join(missing_settings)
            content = f"{content}{separator}{appended_settings}\n"
        env_file.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(
            f"Could not safely prepare .env/.gitignore: {exc}"
        ) from exc


def _stateful_service_env_keys(scan: ScanResult) -> set[str]:
    detected = set(scan.detected_env_vars)
    required: set[str] = set()
    for service in scan.stateful_services:
        name = service.lower()
        if any(hint in name for hint in ("redis", "valkey", "keydb")):
            candidates = {key for key in detected if "REDIS" in key or "VALKEY" in key}
            required.update(candidates or {"REDIS_URL"})
        elif any(
            hint in name
            for hint in (
                "db", "database", "postgres", "mysql", "maria", "mongo",
                "couchbase", "cassandra",
            )
        ):
            candidates = {
                key for key in detected
                if any(hint in key for hint in ("DATABASE", "POSTGRES", "MYSQL", "MONGO"))
            }
            required.update(candidates or {"DATABASE_URL"})
        elif any(hint in name for hint in ("rabbit", "kafka", "broker")):
            candidates = {
                key for key in detected
                if any(hint in key for hint in ("RABBIT", "KAFKA", "BROKER"))
            }
            required.update(candidates or {f"{service.upper().replace('-', '_')}_URL"})
        else:
            candidates = {
                key for key in detected
                if service.upper().replace("-", "_") in key
            }
            required.update(
                candidates or {f"{service.upper().replace('-', '_')}_URL"}
            )
    return required


def _print_stateful_service_deploy_guidance(
    repo_path: Path,
    repo_label: str,
    port: int,
    environment_variables: Optional[list[str]] = None,
    connection_variables: Optional[set[str]] = None,
) -> None:
    source = repo_label
    valid_url, _ = validate_github_url(source)
    if not valid_url:
        source = "https://github.com/OWNER/REPOSITORY.git"
        app_name = repo_path.name.lower().replace("_", "-")
    else:
        repository_name = urlsplit(source).path.rstrip("/").rsplit("/", 1)[-1]
        if repository_name.lower().endswith(".git"):
            repository_name = repository_name[:-4]
        app_name = repository_name.lower().replace("_", "-")
    click.echo(
        "\n⚠️ Compose stateful services are not included in the application container "
        "and EBKit does not provision managed services."
    )
    click.echo(
        "Provision the required database/cache/broker separately, configure network "
        "access, then fill in the matching connection values in the gitignored .env."
    )
    click.echo(
        "Blank connection settings were added where none were detected; existing "
        ".env values were preserved."
    )
    settings = sorted(set(environment_variables or []) - {"PORT"})
    connections = connection_variables or set()
    click.echo("\nSettings to configure in .env (use values from your service provider):")
    for key in settings:
        purpose = "external service connection value" if key in connections else "application setting; check your project documentation"
        click.echo(f"  {key}=<YOUR_{key}>  ({purpose})")
    click.echo(f"  PORT={port}")
    click.echo("Connection URLs must use endpoints reachable from AWS, with the provider's credentials and TLS settings.")
    if "POSTGRES_URL" in settings:
        click.echo("  POSTGRES_URL format: postgresql://USER:PASSWORD@HOST:5432/DATABASE?sslmode=require")
    if "REDIS_URL" in settings:
        click.echo("  REDIS_URL format: rediss://USER:PASSWORD@HOST:PORT")
    click.echo(
        "After pushing the root Dockerfile to GitHub, deploy with:\n"
        f"  ebkit deploy {source} --app {app_name} "
        f"--environment {app_name}-cluster --port {port} --env-file .env"
    )
    if settings:
        inline_settings = " ".join(f'--env "{key}=<YOUR_{key}>"' for key in settings)
        click.echo(
            "Or pass application settings directly (replace the placeholders):\n"
            f"  ebkit deploy {source} --app {app_name} "
            f"--environment {app_name}-cluster --port {port} {inline_settings}"
        )
        click.echo("You can also combine --env-file .env with repeated --env KEY=VALUE overrides.")


# ---------------------------------------------------------------------------
# Display helper: EBReady Deployment Plan (canonical format preserved)
# ---------------------------------------------------------------------------


def _print_ebready_plan(
    cfg: DeploymentConfig,
    repo_label: str,
) -> None:
    """
    Display the EBReady Deployment Plan.
    Uses + / - ASCII symbols (safe on Windows cp1252).
    """
    plan = cfg.get_artifact_plan()

    click.echo("\nEBReady Deployment Plan")
    click.echo("-" * 55)
    click.echo(f"  Repository      : {repo_label}")
    click.echo(f"  Language        : {cfg.language.value}")
    click.echo(f"  Framework       : {cfg.framework or 'none'}")
    click.echo(f"  Runtime         : {cfg.runtime_version or 'unknown'}")
    click.echo(f"  Port            : {cfg.port}")
    click.echo(f"  Architecture    : {cfg.architecture}")
    click.echo(f"  Container strat : {cfg.container_strategy.value}")
    click.echo(f"  EB Mode         : Cluster Mode")

    cbc = cfg.cluster_build_config
    cec = cfg.cluster_environment_config
    if cec:
        replicas = f"{cec.min_replicas}-{cec.max_replicas}"
        probe_path = cec.readiness_probe.path if cec.readiness_probe else cfg.health_check_path
        click.echo(f"  Cluster         : service_port={cec.service_port}, readiness={probe_path}, replicas={replicas}")
    elif cbc:
        click.echo(f"  Cluster build   : arch={cbc.architecture}")

    click.echo()
    click.echo("  Artifacts:")
    for name, req in plan.artifacts.items():
        if req.required:
            symbol = "+"
            label = "[REQUIRED]"
        elif req.optional:
            symbol = "~"
            label = "[OPTIONAL]"
        else:
            symbol = "-"
            label = "[not required]"
        click.echo(f"    {symbol} {req.target_path:<22} {label:<16}  {req.reason[:60]}")

    if cfg.uncertainties:
        click.echo("\n  [!] AI Uncertainties:")
        for u in cfg.uncertainties:
            click.echo(f"       * {u.field_name}: {u.reason}")
    click.echo()


# ---------------------------------------------------------------------------
# Interactive Wizard Helpers
# ---------------------------------------------------------------------------


def _prompt_project_source() -> tuple[Path, Optional[str], str]:
    """
    Prompt user for project source.
    Returns (repo_path, cleanup_path, repo_label); GitHub clones are retained.
    """
    click.echo("🚀 Welcome to EBReady\n")
    click.echo("Where is your project?\n")
    click.echo("1. Current directory")
    click.echo("2. GitHub repository")
    click.echo("3. Local project path\n")

    choice = click.prompt("Select", default="1", show_default=True).strip()

    if choice == "1":
        click.echo("Project path: .")
        repo_path = Path(".").resolve()
        return repo_path, None, "."

    if choice == "2":
        click.echo("GitHub repository URL:")
        raw_url = click.prompt(">", prompt_suffix=" ").strip()
        is_valid, err_msg = validate_github_url(raw_url)
        if not is_valid:
            click.echo(f"\n❌ Invalid GitHub repository URL: {err_msg}", err=True)
            sys.exit(1)

        repo_path = _clone_repo_to_local_folder(raw_url)
        return repo_path, None, raw_url

    if choice == "3":
        click.echo("Local project path:")
        raw_path = click.prompt(">", prompt_suffix=" ").strip()
        local_p = Path(raw_path).resolve()
        if not local_p.exists():
            click.echo(f"\n❌ Project path does not exist: {raw_path}", err=True)
            sys.exit(1)
        if not local_p.is_dir():
            click.echo(f"\n❌ Project path is not a directory: {raw_path}", err=True)
            sys.exit(1)
        return local_p, None, str(local_p)

    click.echo(f"\n❌ Invalid selection: '{choice}'. Please select 1, 2, or 3.", err=True)
    sys.exit(1)


def _clone_repo_to_local_folder(url: str) -> Path:
    """Clone a GitHub repository into a new folder under the current directory."""
    repo_name = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    if repo_name.lower().endswith(".git"):
        repo_name = repo_name[:-4]
    if not repo_name or repo_name in {".", ".."}:
        raise click.ClickException("Could not determine a local folder name from the GitHub URL.")

    destination = Path.cwd() / repo_name
    try:
        destination.mkdir()
    except FileExistsError:
        raise click.ClickException(
            f"Cannot clone repository because '{destination}' already exists. "
            "Move or rename that folder, then run `ebkit init` again."
        ) from None
    except OSError as exc:
        raise click.ClickException(f"Could not create repository folder '{destination}': {exc}") from exc

    try:
        click.echo(f"Cloning repository into {destination}...")
        safe_clone_repo(url, dest_dir=destination)
    except Exception as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise click.ClickException(f"Failed to clone GitHub repository: {exc}") from exc
    return destination


def _check_gemini_key() -> None:
    """Verify that GEMINI_API_KEY or GOOGLE_API_KEY is present."""
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        click.echo(
            "\n❌ GEMINI_API_KEY is not set.\n\n"
            "Set it in your environment and run `ebkit init` again.",
            err=True,
        )
        sys.exit(1)
    if not os.environ.get("GEMINI_API_KEY") and os.environ.get("GOOGLE_API_KEY"):
        os.environ["GEMINI_API_KEY"] = os.environ["GOOGLE_API_KEY"]


def _choose_local_build(build: Optional[bool], runtime: Optional[bool], yes: bool) -> bool:
    """Only build after an explicit flag or an affirmative interactive answer."""
    if build is not None:
        return build
    if runtime is True:
        return True
    if yes:
        return False
    return click.confirm("Build the Docker image locally?", default=False)


def _show_source_dependency_fixes(result: ScoutResult) -> None:
    """Explain source findings without silently changing application dependencies."""
    click.echo("\nDeployment files were generated. Vulnerable packages need attention:")
    packages: dict[str, dict[str, set[str]]] = {}
    for finding in result.details:
        for package in finding.get("packages", []):
            entry = packages.setdefault(package, {"fixes": set(), "paths": set(), "cves": set()})
            entry["fixes"].add(str(finding.get("fixed_version") or "not reported"))
            entry["paths"].update(path for path in finding.get("paths", []) if path)
            entry["cves"].add(str(finding.get("id", "unknown")))
    for package, entry in sorted(packages.items()):
        click.echo(f"  * {package}: {len(entry['cves'])} CVEs")
        click.echo(f"    Manifest: {', '.join(sorted(entry['paths'])) or 'not reported'}")
        click.echo(f"    Reported fixed versions: {', '.join(sorted(entry['fixes']))}")
    click.echo("\nAll reported vulnerability findings:")
    for finding in result.details:
        click.echo(f"  * {finding.get('id', 'unknown')} [{finding.get('severity', 'unknown')}]")
        click.echo(f"    Package: {', '.join(finding.get('packages', [])) or 'not reported'}")
        click.echo(f"    Location: {', '.join(finding.get('paths', [])) or 'not reported'}")
        click.echo(f"    Fixed version: {finding.get('fixed_version') or 'not reported'}")
        if finding.get("url"):
            click.echo(f"    Advisory: {finding['url']}")
    if not result.details:
        click.echo("  No structured findings available; review the Scout scan error above.")
    click.echo("Update affected dependencies and lockfiles or base images, test the application, then rerun init.")
    click.echo("Some fixes may require a major upgrade; findings marked 'not fixed' need further review.")
    click.echo("Generating another Dockerfile will not fix source dependency CVEs. The security gate remains enforced.")


def _prompt_ai_provider(default_choice: str = "1") -> str:
    """Prompt user for AI Provider choice."""
    click.echo("\n🤖 AI Provider\n")
    click.echo("1. Gemini — ✅ Available")
    click.echo("2. OpenAI — 🔜 Coming Later")
    click.echo("3. Claude — 🔜 Coming Later")
    click.echo("4. Groq API — 🔜 Coming Later\n")

    choice = click.prompt("Select", default=default_choice, show_default=True).strip()

    if choice == "1":
        _check_gemini_key()
        return "gemini"
    if choice == "2":
        click.echo("\n🔜 OpenAI is coming later. Currently, only Gemini is available.", err=True)
        sys.exit(1)
    if choice == "3":
        click.echo("\n🔜 Claude is coming later. Currently, only Gemini is available.", err=True)
        sys.exit(1)
    if choice == "4":
        click.echo("\n🔜 Groq API is coming later. Currently, only Gemini is available.", err=True)
        sys.exit(1)

    click.echo(f"\n❌ Invalid AI provider selection: '{choice}'. Please select 1, 2, 3, or 4.", err=True)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Command definition
# ---------------------------------------------------------------------------


@click.command("init")
@click.option(
    "--path",
    "-p",
    default=None,
    help="Path to the project repository root.",
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
)
@click.option(
    "--repo",
    "-r",
    default=None,
    help="GitHub or Git repository URL to clone and analyze.",
)
@click.option(
    "--output",
    "-o",
    default=None,
    help="Output directory for generated files (defaults to project root).",
    type=click.Path(file_okay=False, resolve_path=True),
)
@click.option(
    "--analyzer",
    "-a",
    default=None,
    type=click.Choice(["gemini", "google", "openai", "claude", "groq"], case_sensitive=False),
    help="AI analyzer backend to use. Auto-detected from env vars if not set.",
)
@click.option(
    "--port",
    default=None,
    type=int,
    help="Application port (if not specified, auto-detected from project or prompted).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Analyze and display the deployment plan without writing any files.",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    default=False,
    help="Overwrite existing deployment files without prompting.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Skip human confirmation prompts (non-interactive mode).",
)
@click.option(
    "--build/--no-build",
    default=None,
    help="Build locally or skip the build prompt (interactive default: No; --yes skips building).",
)
@click.option(
    "--runtime/--no-runtime",
    default=None,
    help="After Docker build, start container and probe / and /health for HTTP 200.",
)
@click.option(
    "--scout/--no-scout",
    default=None,
    help="Run Docker Scout (source dependencies by default; built image with --build).",
)
@click.option(
    "--max-critical",
    default=0,
    type=int,
    help="Maximum allowed critical CVEs for Docker Scout security gate.",
)
@click.option(
    "--max-high",
    default=5,
    type=int,
    help="Maximum allowed high CVEs for Docker Scout security gate.",
)
@click.option(
    "--max-repair-attempts",
    default=2,
    type=int,
    help="Maximum automatic repair attempts using Docker AI (Gordon) [default: 2].",
)
def init_command(
    path: Optional[str],
    repo: Optional[str],
    output: Optional[str],
    analyzer: Optional[str],
    port: Optional[int],
    dry_run: bool,
    force: bool,
    yes: bool,
    build: Optional[bool],
    runtime: bool,
    scout: Optional[bool],
    max_critical: int,
    max_high: int,
    max_repair_attempts: int,
) -> None:
    """
    Initialize EBReady deployment kit with interactive setup wizard.
    """
    tmp_clone_dir: Optional[str] = None
    repo_label: str = ""
    if build is False and runtime is True:
        raise click.UsageError("--runtime cannot be combined with --no-build.")
    saved_cfg = load_config()

    # ── Non-interactive / CI Mode Validation ───────────────────────────────
    if yes:
        if not path and not repo:
            click.echo("\n❌ Non-interactive mode requires a project path or repository.", err=True)
            sys.exit(1)

        if repo:
            is_valid, err_msg = validate_github_url(repo)
            if not is_valid:
                click.echo(f"\n❌ Invalid GitHub repository URL: {err_msg}", err=True)
                sys.exit(1)
            repo_path = _clone_repo_to_local_folder(repo)
            repo_label = repo
        else:
            repo_path = Path(path).resolve()
            repo_label = str(repo_path)

        chosen_analyzer = (analyzer or "").lower()
        if chosen_analyzer in ("openai", "claude", "groq"):
            click.echo(f"\n🔜 {chosen_analyzer.capitalize()} is coming later. Currently, only Gemini is available.", err=True)
            sys.exit(1)
        if not chosen_analyzer:
            if saved_cfg and saved_cfg.ai_provider.lower() in ("gemini", "google"):
                chosen_analyzer = saved_cfg.ai_provider
            else:
                chosen_analyzer = "gemini"

        if chosen_analyzer in ("gemini", "google"):
            _check_gemini_key()

    else:
        # ── Interactive Mode ────────────────────────────────────────────────
        if repo:
            is_valid, err_msg = validate_github_url(repo)
            if not is_valid:
                click.echo(f"\n❌ Invalid GitHub repository URL: {err_msg}", err=True)
                sys.exit(1)
            repo_path = _clone_repo_to_local_folder(repo)
            repo_label = repo
        elif path:
            repo_path = Path(path).resolve()
            repo_label = str(repo_path)
        else:
            repo_path, tmp_clone_dir, repo_label = _prompt_project_source()

        # Check saved configuration
        if saved_cfg and not analyzer:
            click.echo("\nUsing saved configuration:\n")
            click.echo(f"AI Provider: {saved_cfg.ai_provider_display}\n")

            continue_saved = click.confirm("Continue?", default=True)
            if continue_saved:
                chosen_analyzer = saved_cfg.ai_provider
                if chosen_analyzer in ("openai", "claude", "groq"):
                    click.echo(f"\n🔜 {chosen_analyzer.capitalize()} is coming later. Currently, only Gemini is available.", err=True)
                    sys.exit(1)
                if chosen_analyzer in ("gemini", "google"):
                    _check_gemini_key()
            else:
                chosen_analyzer = _prompt_ai_provider(default_choice="1")
                save_config(
                    EBKitConfig(
                        ai_provider=chosen_analyzer,
                    )
                )
        else:
            if analyzer:
                chosen_analyzer = analyzer.lower()
                if chosen_analyzer in ("openai", "claude", "groq"):
                    click.echo(f"\n🔜 {chosen_analyzer.capitalize()} is coming later. Currently, only Gemini is available.", err=True)
                    sys.exit(1)
                if chosen_analyzer in ("gemini", "google"):
                    _check_gemini_key()
            else:
                chosen_analyzer = _prompt_ai_provider()

            save_config(
                EBKitConfig(
                    ai_provider=chosen_analyzer,
                )
            )

    dest_path = Path(output) if output else repo_path

    # ── Step 1: Scanner ─────────────────────────────────────────────────────
    click.echo("\n🔍 Scanning project...\n")
    try:
        scanner = ProjectScanner(repo_path)
        scan = scanner.scan()
    except Exception as exc:
        click.echo(f"\n❌ Failed to scan project.\n\nProblem:\n{exc}\n\nFix:\nCheck directory permissions and source files.", err=True)
        if tmp_clone_dir:
            shutil.rmtree(tmp_clone_dir, ignore_errors=True)
        sys.exit(1)

    # Show scanning progress details
    shown_details = 0
    if scan.language:
        click.echo(f"✓ {scan.language.capitalize()} detected")
        shown_details += 1
    if scan.framework:
        click.echo(f"✓ {scan.framework.capitalize()} detected")
        shown_details += 1
    for dep in scan.dependency_files:
        click.echo(f"✓ {dep} detected")
        shown_details += 1
    if scan.entrypoint:
        click.echo(f"✓ {scan.entrypoint} detected")
        shown_details += 1
    if scan.detected_port:
        click.echo(f"✓ Port {scan.detected_port} detected")
        shown_details += 1
    if scan.architecture:
        click.echo(f"✓ Architecture: {scan.architecture}")
        if scan.architecture == "MULTI_TIER" and scan.services:
            click.echo("  Services:")
            for s in scan.services:
                click.echo(f"  ✓ {s}")
        shown_details += 1
    if scan.architecture == "MULTI_TIER":
        if scan.stateful_services:
            click.echo(
                "✓ Compose stateful services detected: "
                + ", ".join(scan.stateful_services)
            )
        click.echo(
            "\n⚠️ Multi-tier project detected. EBKit will ask AI to generate and validate "
            "one root-level Dockerfile for its application services."
        )
    if scan.existing_dockerfile:
        click.echo("✓ Dockerfile detected")
        shown_details += 1
    elif scan.existing_docker_compose:
        click.echo("✓ Docker Compose detected")
        shown_details += 1
    if shown_details == 0:
        click.echo("✓ Project detected")

    # Port conflict detection
    if scan.port_conflict:
        click.echo(
            f"\n❌ Conflicting port declarations in Dockerfile:\n{scan.port_conflict_details}\n\n"
            "Fix:\nEnsure Dockerfile EXPOSE port matches CMD/ENTRYPOINT port.",
            err=True,
        )
        if tmp_clone_dir:
            shutil.rmtree(tmp_clone_dir, ignore_errors=True)
        sys.exit(1)

    # Resolve application port
    resolved_port: Optional[int] = port or scan.detected_port
    if resolved_port is None:
        if yes:
            # In non-interactive mode, fall back to 8080 — the AI analysis will
            # confirm or override this with the correct port.
            resolved_port = 8080
            click.echo("⚠️ Application port could not be auto-detected — defaulting to 8080.")
        else:
            click.echo("\n⚠️ Application port could not be determined from project metadata.")
            resolved_port = click.prompt("Please enter application port", type=int)

    scan.detected_port = resolved_port

    # ── Step 2: AI Analysis (Gemini) ────────────────────────────────────────
    ai_label = "Gemini"
    click.echo(f"\n🤖 Running {ai_label} analysis...\n")
    try:
        ai = get_analyzer(prefer=chosen_analyzer)
        if scan.stateful_services:
            scan.detected_env_vars = sorted(
                set(scan.detected_env_vars) | _stateful_service_env_keys(scan)
            )
        config = ai.analyze(scan)
        if scan.architecture == "MULTI_TIER":
            aws_credential_keys = {
                "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY",
                "AWS_SESSION_TOKEN",
            }
            config.environment_variables = sorted(
                (set(config.environment_variables) | set(scan.detected_env_vars))
                - aws_credential_keys
            )
            config.cluster_environment_config.environment_variables = list(
                config.environment_variables
            )
            env_example = config.get_artifact_plan().artifacts.get("env_example")
            if env_example:
                env_example.required = True
                env_example.optional = False
            if scan.detected_env_vars:
                click.echo(
                    "✓ External service/application variable names added to .env.example "
                    "(values remain blank)."
                )
        # Ensure resolved project port is strictly respected
        if config.port != resolved_port:
            config.port = resolved_port
            if config.cluster_environment_config:
                config.cluster_environment_config.service_port = resolved_port
                if config.cluster_environment_config.readiness_probe:
                    config.cluster_environment_config.readiness_probe.port = resolved_port
                if config.cluster_environment_config.liveness_probe:
                    config.cluster_environment_config.liveness_probe.port = resolved_port
            # Update start command port if applicable
            import re
            config.start_command = re.sub(r"--port\s+\d+", f"--port {resolved_port}", config.start_command)
            config.start_command = re.sub(r":\d{2,5}\b", f":{resolved_port}", config.start_command)

        click.echo("✓ DeploymentConfig generated")
        click.echo("✓ Pydantic validation passed")
        click.echo("✓ Configuration validated")
    except Exception as exc:
        click.echo(
            f"\n❌ Deployment configuration is invalid.\n\nProblem:\n{exc}\n\n"
            "Fix:\nReview the project configuration and run `ebkit init` again.",
            err=True,
        )
        if tmp_clone_dir:
            shutil.rmtree(tmp_clone_dir, ignore_errors=True)
        sys.exit(1)

    # ── Dry-run Check ───────────────────────────────────────────────────────
    if dry_run:
        _print_ebready_plan(config, repo_label)
        click.echo("[DRY RUN] No files were written.")
        if tmp_clone_dir:
            shutil.rmtree(tmp_clone_dir, ignore_errors=True)
        sys.exit(0)

    # ── Step 3: Jinja2 Templates Rendering & Docker AI Generation ───────────
    try:
        renderer = DeploymentKitRenderer()
        kit = renderer.render(config)
    except Exception as exc:
        click.echo(
            f"\n❌ Template rendering failed.\n\nProblem:\n{exc}\n\n"
            "Fix:\nReview the deployment configuration and run `ebkit init` again.",
            err=True,
        )
        if tmp_clone_dir:
            shutil.rmtree(tmp_clone_dir, ignore_errors=True)
        sys.exit(1)

    # Docker AI (Gordon) Dockerfile Generation
    docker_ai = DockerAIService()
    if docker_ai.is_available() or scan.architecture == "MULTI_TIER":
        click.echo("\n🤖 AI generating the root Dockerfile...\n")
        gordon_df, gen_msg = docker_ai.generate_dockerfile(config, scan, working_dir=repo_path)
        if gordon_df:
            kit.files["Dockerfile"] = gordon_df
            click.echo(f"✓ Root Dockerfile generated: {gen_msg}")
        else:
            if scan.architecture == "MULTI_TIER":
                click.echo(
                    f"\n❌ Could not generate a complete multi-tier Dockerfile: {gen_msg}\n"
                    "No generated Dockerfile was written. Review the project services "
                    "and try again.",
                    err=True,
                )
                if tmp_clone_dir:
                    shutil.rmtree(tmp_clone_dir, ignore_errors=True)
                sys.exit(1)
            click.echo(f"⚠️ Docker AI generation notice: {gen_msg} (using template)")
    else:
        click.echo("\n⚠️ Docker AI (Gordon) is unavailable. Using template generation.")

    # ── Step 4: Safe File Writing ───────────────────────────────────────────
    click.echo("\n📦 Generating deployment kit...\n")
    written: dict[str, str] = {}
    skipped: dict[str, str] = dict(kit.skipped)

    for rel_path, content in kit.files.items():
        ok, status = _safe_write(dest_path, rel_path, content, force=(force or yes))
        if ok:
            written[rel_path] = status
            click.echo(f"✓ {rel_path}")
        else:
            skipped[rel_path] = status

    for rel_path in kit.skipped:
        click.echo(f"✓ {rel_path} (if required)")

    # If output dest_path is separate from repo_path, populate dest_path with source files
    if dest_path.resolve() != repo_path.resolve():
        for item in repo_path.iterdir():
            if item.name.startswith(".git"):
                continue
            dest_item = dest_path / item.name
            if item.is_dir():
                if not dest_item.exists():
                    shutil.copytree(item, dest_item, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest_item)

    # Also write to repo_path if dest_path is separate, so Docker build can find files
    if dest_path.resolve() != repo_path.resolve():
        for rel_path, content in kit.files.items():
            dest_file = dest_path / rel_path
            final_content = dest_file.read_text(encoding="utf-8") if dest_file.exists() else content
            _safe_write(repo_path, rel_path, final_content, force=True)

    # ── Step 5: Artifact Validation ─────────────────────────────────────────
    click.echo("\n🔎 Validating generated artifacts...\n")
    validator = CrossFileValidator()
    val_report = validator.validate(kit)

    if not val_report.is_valid:
        err_msg = "\n".join(f"  * {e}" for e in val_report.errors)
        click.echo(
            f"\n❌ Deployment configuration is invalid.\n\nProblem:\n{err_msg}\n\n"
            "Fix:\nReview the project configuration and run `ebkit init` again.",
            err=True,
        )
        if tmp_clone_dir:
            shutil.rmtree(tmp_clone_dir, ignore_errors=True)
        sys.exit(1)

    click.echo("✓ Dockerfile validation")
    click.echo("✓ Cross-file validation")
    click.echo("✓ Secret validation")

    # ── Step 6: Docker Build, Runtime & Scout with Docker AI Recovery ────────
    build_result: Optional[BuildResult] = None
    runtime_result: Optional[RuntimeResult] = None
    scout_result: Optional[ScoutResult] = None

    tag = f"ebready-{repo_path.name.lower()}:prod"
    builder = DockerBuildValidator()
    should_build = _choose_local_build(build, runtime, yes)
    docker_available = builder.is_docker_available() if should_build else False

    scout_validator = DockerScoutValidator(
        max_critical=max_critical,
        max_high=max_high,
    )
    should_scout = scout is not False
    scout_available = scout_validator.is_scout_available() if should_scout else False

    repair_attempt = 0

    dockerfile_path = repo_path / "Dockerfile"
    dockerfile_present = (
        dockerfile_path.exists()
        or (dest_path / "Dockerfile").exists()
        or "Dockerfile" in kit.files
    )

    while True:
        # ── 6.1 Docker Build ────────────────────────────────────────────────
        if should_build and dockerfile_present:
            click.echo("\n🐳 Docker Build\n")
            build_result = builder.build(
                repo_path,
                image_tag=tag,
                platform=config.platform.value,
            )
            if build_result.success:
                click.echo(f"✓ {config.platform.value} image built")
                click.echo("✓ Build successful")
            else:
                click.echo("✗ Build failed")
                if repair_attempt < max_repair_attempts:
                    if not docker_ai.is_available():
                        click.echo("\n⚠️ Docker AI (Gordon) is unavailable. Automatic error recovery cannot proceed.")
                        click.echo(
                            f"\n❌ Docker build failed.\n\nReason:\n{build_result.error or 'Build process exited with error.'}\n\n"
                            "EBReady cannot mark this project as ready for deployment.",
                            err=True,
                        )
                        click.echo("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                        click.echo("❌ PROJECT NOT READY")
                        click.echo("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                        if tmp_clone_dir:
                            shutil.rmtree(tmp_clone_dir, ignore_errors=True)
                        sys.exit(1)

                    repair_attempt += 1
                    df_to_repair = (
                        dockerfile_path.read_text(encoding="utf-8")
                        if dockerfile_path.exists()
                        else kit.files.get("Dockerfile", "")
                    )
                    click.echo("\n🤖 Docker AI Recovery\n")
                    click.echo(f"🔧 Docker AI (Gordon) diagnosing build failure and repairing Dockerfile (attempt {repair_attempt}/{max_repair_attempts})...\n")
                    repaired_df, diagnosis = docker_ai.diagnose_and_repair(
                        error_type="Docker Build Failure",
                        error_details=f"Build Error:\n{build_result.error}\nBuild Output:\n{build_result.output[-1500:]}",
                        current_dockerfile=df_to_repair,
                        config=config,
                        working_dir=repo_path,
                        attempt=repair_attempt,
                    )
                    if repaired_df:
                        click.echo("✓ Diagnosis")
                        if diagnosis:
                            click.echo(f"   Diagnosis: {diagnosis}")
                        click.echo("✓ Safe repair")
                        # Pre-rebuild safety validation
                        kit.files["Dockerfile"] = repaired_df
                        pre_val = validator.validate(kit)
                        if not pre_val.is_valid:
                            err_msg = "\n".join(f"  * {e}" for e in pre_val.errors)
                            click.echo(f"❌ Repaired Dockerfile failed safety validation:\n{err_msg}", err=True)
                            break
                        _safe_write(dest_path, "Dockerfile", repaired_df, force=True)
                        if dest_path.resolve() != repo_path.resolve():
                            _safe_write(repo_path, "Dockerfile", repaired_df, force=True)
                        click.echo("✓ Dockerfile repaired by Docker AI (Gordon)")
                        click.echo("✓ Safety validation passed before rebuilding")
                        continue  # Re-attempt build
                    else:
                        click.echo(f"❌ Docker AI recovery failed: {diagnosis}")
                        break
                else:
                    click.echo(
                        f"\n❌ Docker build failed after {repair_attempt} repair attempts.\n\nReason:\n{build_result.error or 'Build process exited with error.'}\n\n"
                        "EBReady cannot mark this project as ready for deployment.",
                        err=True,
                    )
                    break

        # ── 6.2 Docker Runtime Health Check ─────────────────────────────────
        should_runtime = (
            runtime if runtime is not None
            else (build_result is not None and build_result.success and docker_available)
        )
        if should_runtime and build_result and build_result.success:
            click.echo("\n🐳 Runtime\n")
            click.echo("🔍 Container Runtime Health Check\n")
            runtime_validator = DockerRuntimeValidator()
            runtime_result = runtime_validator.validate(
                image_tag=tag,
                port=config.port,
                health_check_path=config.health_check_path,
            )
            if runtime_result.passed:
                click.echo("✓ Health check passed")
                click.echo("✓ Container is running")
                status_detail = f"HTTP {runtime_result.status_code}" if runtime_result.status_code else "HTTP reachable"
                live_url = runtime_result.url or f"http://localhost:{runtime_result.host_port}/"
                click.echo(f"✓ Application is responding ({status_detail} at {live_url})")
                click.echo(f"\n🌐 Application running at: {live_url}")
            else:
                # ── Container exited after health check — infrastructure crash, NOT a Dockerfile bug ──
                if runtime_result.container_exited_after_check:
                    click.echo("✗ Container exited after health check")
                    click.echo(f"   Exit reason: {runtime_result.exit_reason}", err=True)
                    if runtime_result.logs:
                        click.echo(f"   Container logs:\n{runtime_result.logs[-1000:]}", err=True)
                    click.echo(
                        "\n⚠️ The container crashed after the health check passed.\n"
                        "This is an application/infrastructure failure, not a Dockerfile syntax issue.\n"
                        "Docker AI will NOT be invoked (it cannot fix a runtime crash by changing the Dockerfile).",
                        err=True,
                    )
                    click.echo("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    click.echo("❌ PROJECT NOT READY")
                    click.echo("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    if tmp_clone_dir:
                        shutil.rmtree(tmp_clone_dir, ignore_errors=True)
                    sys.exit(1)

                # If the runtime error is due to a host-port conflict, do NOT ask Docker AI to repair
                if runtime_result.error and (
                    "port is already allocated" in runtime_result.error.lower()
                    or "address already in use" in runtime_result.error.lower()
                ):
                    click.echo("⚠️ Host port conflict detected. Retrying with a free host port...")
                    # Re-run runtime validator which will allocate another free host port
                    runtime_result = runtime_validator.validate(
                        image_tag=tag,
                        port=config.port,
                        health_check_path=config.health_check_path,
                    )
                    if runtime_result.passed:
                        click.echo("✓ Health check passed")
                        click.echo("✓ Container is running")
                        status_detail = f"HTTP {runtime_result.status_code}" if runtime_result.status_code else "HTTP reachable"
                        live_url = runtime_result.url or f"http://localhost:{runtime_result.host_port}/"
                        click.echo(f"✓ Application is responding ({status_detail} at {live_url})")
                        click.echo(f"\n🌐 Application running at: {live_url}")

                if not runtime_result.passed:
                    click.echo("✗ Health check failed")
                    if repair_attempt < max_repair_attempts:
                        if not docker_ai.is_available():
                            click.echo("\n⚠️ Docker AI (Gordon) is unavailable. Automatic error recovery cannot proceed.")
                            click.echo(f"❌ Container runtime health check failed: {runtime_result.error}", err=True)
                            click.echo("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                            click.echo("❌ PROJECT NOT READY")
                            click.echo("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                            if tmp_clone_dir:
                                shutil.rmtree(tmp_clone_dir, ignore_errors=True)
                            sys.exit(1)

                        repair_attempt += 1
                        df_to_repair = (
                            dockerfile_path.read_text(encoding="utf-8")
                            if dockerfile_path.exists()
                            else kit.files.get("Dockerfile", "")
                        )
                        click.echo("\n🤖 Docker AI Recovery\n")
                        click.echo(f"🔧 Docker AI (Gordon) diagnosing runtime failure and repairing Dockerfile (attempt {repair_attempt}/{max_repair_attempts})...\n")
                        repaired_df, diagnosis = docker_ai.diagnose_and_repair(
                            error_type="Docker Container Runtime Health Check Failure",
                            error_details=f"Runtime Probe Error:\n{runtime_result.error}\nContainer Logs:\n{runtime_result.logs[-1000:]}",
                            current_dockerfile=df_to_repair,
                            config=config,
                            working_dir=repo_path,
                            attempt=repair_attempt,
                        )
                        if repaired_df:
                            click.echo("✓ Diagnosis")
                            if diagnosis:
                                click.echo(f"   Diagnosis: {diagnosis}")
                            click.echo("✓ Safe repair")
                            kit.files["Dockerfile"] = repaired_df
                            pre_val = validator.validate(kit)
                            if not pre_val.is_valid:
                                err_msg = "\n".join(f"  * {e}" for e in pre_val.errors)
                                click.echo(f"❌ Repaired Dockerfile failed safety validation:\n{err_msg}", err=True)
                                break
                            _safe_write(dest_path, "Dockerfile", repaired_df, force=True)
                            if dest_path.resolve() != repo_path.resolve():
                                _safe_write(repo_path, "Dockerfile", repaired_df, force=True)
                            click.echo("✓ Dockerfile repaired by Docker AI (Gordon)")
                            click.echo("✓ Safety validation passed before rebuilding")
                            continue  # Re-attempt from build
                        else:
                            click.echo(f"❌ Docker AI recovery failed: {diagnosis}")
                            break
                    else:
                        click.echo(f"❌ Container runtime health check failed after {repair_attempt} repair attempts: {runtime_result.error}", err=True)
                        break

        # ── 6.3 Docker Scout Security Gate ──────────────────────────────────
        should_scout = (
            scout if scout is not None
            else (build_result is not None and build_result.success and docker_available)
        )
        if should_scout and (build_result is None or not build_result.success):
            should_scout = False

        if should_scout:
            if scout_available:
                click.echo("\n🛡 Docker Scout\n")
                scout_result = scout_validator.scan(tag)
                click.echo("✓ Security scan completed")
                click.echo(f"✓ {scout_result.critical_count} Critical")
                click.echo(f"✓ {scout_result.high_count} High")
                if scout_result.gate_passed:
                    click.echo("✓ Security gate passed")
                else:
                    # Critical vulnerabilities must be 0
                    if scout_result.critical_count > 0 and repair_attempt < max_repair_attempts:
                        if not docker_ai.is_available():
                            click.echo("\n⚠️ Docker AI (Gordon) is unavailable. Automatic error recovery cannot proceed.")
                            click.echo(f"❌ Security gate failed: {scout_result.gate_reason}", err=True)
                            break

                        repair_attempt += 1
                        df_to_repair = (
                            dockerfile_path.read_text(encoding="utf-8")
                            if dockerfile_path.exists()
                            else kit.files.get("Dockerfile", "")
                        )
                        click.echo("\n🤖 Docker AI Recovery\n")
                        click.echo(f"🔧 Docker AI (Gordon) diagnosing security vulnerability and repairing Dockerfile (attempt {repair_attempt}/{max_repair_attempts})...\n")
                        repaired_df, diagnosis = docker_ai.diagnose_and_repair(
                            error_type="Docker Scout Critical Vulnerability Failure",
                            error_details=f"Gate failed: {scout_result.gate_reason}\nCritical: {scout_result.critical_count}, High: {scout_result.high_count}\nSummary: {scout_result.summary}",
                            current_dockerfile=df_to_repair,
                            config=config,
                            working_dir=repo_path,
                            attempt=repair_attempt,
                        )
                        if repaired_df:
                            click.echo("✓ Diagnosis")
                            if diagnosis:
                                click.echo(f"   Diagnosis: {diagnosis}")
                            click.echo("✓ Safe repair")
                            kit.files["Dockerfile"] = repaired_df
                            pre_val = validator.validate(kit)
                            if not pre_val.is_valid:
                                err_msg = "\n".join(f"  * {e}" for e in pre_val.errors)
                                click.echo(f"❌ Repaired Dockerfile failed safety validation:\n{err_msg}", err=True)
                                break
                            _safe_write(dest_path, "Dockerfile", repaired_df, force=True)
                            if dest_path.resolve() != repo_path.resolve():
                                _safe_write(repo_path, "Dockerfile", repaired_df, force=True)
                            click.echo("✓ Dockerfile repaired by Docker AI (Gordon)")
                            click.echo("✓ Safety validation passed before rebuilding")
                            continue  # Re-attempt from build
                        else:
                            click.echo(f"❌ Docker AI recovery failed: {diagnosis}")
                            break
                    else:
                        click.echo(f"❌ Security gate failed: {scout_result.gate_reason}", err=True)
                        break
            else:
                click.echo(
                    "\n⚠️ Docker Scout is unavailable.\n\n"
                    "Security validation: NOT VERIFIED\n\n"
                    "EBReady will not claim that the image passed security validation."
                )

        # Build & test cycle finished cleanly
        break

    if not should_build and scout is not False:
        click.echo("\n🛡 Docker Scout — source dependencies (no image build)\n")
        if scout_available:
            scout_result = scout_validator.scan_source(repo_path)
            click.echo(scout_result.summary)
            if scout_result.gate_passed:
                click.echo("✓ Source dependency security gate passed")
            else:
                click.echo(f"✗ Source dependency security gate failed: {scout_result.gate_reason}")
        else:
            click.echo("Docker Scout is unavailable. Source security validation: NOT VERIFIED.")

    # ── Step 7: Cluster Mode Preflight Evaluation ───────────────────────────
    preflight_validator = ClusterModePreflightValidator()
    if dockerfile_path.exists():
        kit.files["Dockerfile"] = dockerfile_path.read_text(encoding="utf-8")

    require_build = should_build
    build_ok = not should_build or (build_result is not None and build_result.success)

    preflight_report = preflight_validator.evaluate(
        kit=kit,
        build_result=build_result,
        runtime_result=runtime_result,
        scout_result=scout_result,
        require_build=require_build,
    )

    if preflight_report.ready_for_deployment and build_ok:
        click.echo("✓ Cluster Mode preflight\n")
        click.echo("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        click.echo("✅ PROJECT READY FOR DEPLOYMENT" if should_build and scout is not False else "✅ DEPLOYMENT FILES GENERATED")
        click.echo("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        if not should_build:
            click.echo("Local Docker build and runtime checks were not run; the application image was not scanned.")
        if scout is False:
            click.echo("\n⚠️ SECURITY VALIDATION SKIPPED: you bypassed Docker Scout with --no-scout.")
            click.echo("No Scout vulnerability scan was performed. Security readiness is NOT VERIFIED; CVEs may remain.")
        if runtime_result and runtime_result.passed and runtime_result.is_running:
            live_url = runtime_result.url or f"http://localhost:{runtime_result.host_port}/"
            click.echo(f"\n🌐 Application running at: {live_url}")
    else:
        click.echo("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        click.echo("❌ PROJECT NOT READY")
        click.echo("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        if preflight_report.failures:
            click.echo("\nPreflight Failures:")
            for failure in preflight_report.failures:
                click.echo(f"   * {failure}")
        if require_build and not build_ok:
            click.echo("\nDocker build was not successfully executed.")
        if scout_result is not None and not scout_result.gate_passed:
            _show_source_dependency_fixes(scout_result)
            bypass_command = f'  ebkit init --path "{repo_path}" --port {resolved_port} --no-scout'
            if build is not None:
                bypass_command += " --build" if build else " --no-build"
            if runtime is not None:
                bypass_command += " --runtime" if runtime else " --no-runtime"
            click.echo("\nIf you choose to skip Docker Scout, rerun using:")
            click.echo(bypass_command)
            click.echo("This bypass skips security validation; it does not fix the reported CVEs.")
        if tmp_clone_dir:
            shutil.rmtree(tmp_clone_dir, ignore_errors=True)
        sys.exit(1)

    if scan.stateful_services:
        _prepare_stateful_service_env(
            repo_path,
            config.environment_variables,
            resolved_port,
        )
        _print_stateful_service_deploy_guidance(
            repo_path,
            repo_label,
            resolved_port,
            config.environment_variables,
            _stateful_service_env_keys(scan),
        )

    # ── Cleanup temp clone ─────────────────────────────────────────────────
    if tmp_clone_dir:
        shutil.rmtree(tmp_clone_dir, ignore_errors=True)
