"""Local repair review and optional Docker verification."""
import json
import difflib
import subprocess
from pathlib import Path
from uuid import uuid4

import click

from ebkit.doctor import analyze, apply_repairs, public_report
from ebkit.validator.docker_validator import DockerBuildValidator, DockerRuntimeValidator, DockerScoutValidator


@click.command("doctor")
@click.argument("github_url", required=False)
@click.option("--output", type=click.Path(file_okay=False, path_type=Path), help="Empty destination for a public GitHub snapshot (required with a GitHub URL).")
@click.option("--path", "project", type=click.Path(exists=True, file_okay=False, path_type=Path), default=".", show_default=True)
@click.option("--port", type=click.IntRange(1, 65535), default=8080, show_default=True)
@click.option("--fix", is_flag=True, help="Show proposed diffs and ask before applying repairs.")
@click.option("--verify", is_flag=True, help="Build and run the local container, then probe HTTP health.")
@click.option("--production", is_flag=True, help="Enforce the production Dockerfile quality gate.")
@click.option("--require-distroless", is_flag=True, help="Require a supported distroless runtime in the production gate.")
@click.option("--scout", is_flag=True, help="With --verify, scan the built image (zero Critical/High CVEs allowed).")
@click.option("--pin-bases", is_flag=True, help="Resolve real registry digests, review the diff, and approve base-image pins.")
@click.option("--health-path", default="/", show_default=True, help="Preferred HTTP probe path; also tries /health, /healthz and /.")
@click.option("--json", "as_json", is_flag=True, help="Emit a machine-readable report; use --yes to approve fixes/verification.")
@click.option("-y", "--yes", is_flag=True, help="Approve proposed repairs and local build/runtime execution.")
@click.option('--env-file', type=click.Path(exists=True, dir_okay=False, resolve_path=True), help='Private settings for the local runtime check; not baked into the image.')
@click.option('--network', help='Existing Docker network for the local runtime check.')
def doctor_command(github_url, output, project, port, fix, verify, production, require_distroless, scout, pin_bases, health_path, as_json, yes, env_file=None, network=None):
    """Find deployment blockers, review repairs, and verify locally.

    Checks are deterministic and do not require an AI key or AWS credentials.
    Static checks are heuristic; Docker verification and security are reported separately.
    """
    if not health_path.startswith("/") or health_path.startswith("//"):
        raise click.BadParameter("Use a local HTTP path beginning with a single /.", param_hint="--health-path")
    if scout and not verify:
        raise click.UsageError("--scout requires --verify so the actual application image is scanned.")
    try:
        snapshot = None
        if github_url:
            if output is None:
                raise click.UsageError("A GitHub URL requires --output DIRECTORY to keep the reviewed snapshot.")
            if str(project) != ".":
                raise click.UsageError("Choose either a GitHub URL with --output, or --path for a local project.")
            from ebkit.github_source import public_snapshot
            snapshot = public_snapshot(github_url, output)
            project = output.resolve()
            if not as_json:
                click.echo(f"GitHub snapshot: {snapshot['repository']} @ {snapshot['commit'][:12]}")
                click.echo(f"Saved to {project}. Repairs change this local copy; push reviewed changes yourself or deploy a reviewed ZIP.")
        elif output is not None:
            raise click.UsageError("--output is only used with a GitHub URL.")
        plan = analyze(project, port)
        report = public_report(plan)
        if not as_json:
            click.echo("Deployment doctor")
            for item in plan["findings"]:
                click.echo(f"{item['path']}:{item['line']} — {item['message']}")
            for patch in plan["patches"]:
                click.echo(patch["diff"])
        if fix and plan["patches"]:
            approved = yes or (not as_json and click.confirm("Apply these repairs to your local project?", default=False))
            if approved:
                report = apply_repairs(project, plan)
            else:
                report["applied"] = []
                report["repairStatus"] = "awaiting_approval"
        if pin_bases:
            from ebkit.generator.image_pins import pin_base_images
            file=project/'Dockerfile'
            before=file.read_text(encoding='utf-8')
            after=pin_base_images(before)
            if before!=after:
                diff=''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/Dockerfile',tofile='b/Dockerfile'))
                if not as_json:click.echo(diff)
                approved=yes or (not as_json and click.confirm('Apply these registry-resolved base-image pins?',default=False))
                if approved:
                    if file.read_text(encoding='utf-8')!=before:raise ValueError('Dockerfile changed after pin review. Run doctor again.')
                    file.write_text(after,encoding='utf-8')
                else:
                    report['basePinDiff']=diff;report['repairStatus']='awaiting_approval'
        if production or require_distroless:
            from ebkit.validator.production import quality_report
            file = project / 'Dockerfile'
            quality = quality_report(file.read_text(encoding='utf-8') if file.is_file() else '',port,require_distroless)
            report['quality']=quality
            if not quality['passed']:
                report['verification']['static']='failed'
            if not as_json:
                click.echo(f"Production quality: {'passed' if quality['passed'] else 'failed'} — {quality['runtimeImage']}")
                for issue in quality['errors']+quality['warnings']:
                    click.echo(f"  {issue}")
        if verify:
            approved = yes or (not as_json and click.confirm("Build and run this project's code in local Docker for verification?", default=False))
            if approved:
                report["verification"].update(verify_project(project, port, health_path, scan_image=scout, **({'env_file':env_file} if env_file else {}), **({'network':network} if network else {})))
            else:
                report["verification"]["reason"] = "Local execution was not approved."
        if snapshot:
            report["source"] = {"repository": snapshot["repository"], "commit": snapshot["commit"], "path": str(project)}
        if as_json:
            click.echo(json.dumps(report))
        else:
            if report.get("applied"):
                click.echo("Applied: " + ", ".join(report["applied"]))
            click.echo(f"Remaining findings: {len(report['findings'])}")
            for name, status in report["verification"].items():
                click.echo(f"{name}: {status.replace('_', ' ') if isinstance(status, str) else status}")
            if report['verification']['security']!='passed':
                click.echo("Image security is NOT VERIFIED / did not pass. Use --verify --scout to scan the built image.")
            if not verify:
                click.echo(f"Next: ebkit doctor --path {click.format_filename(str(project))} --port {port} --verify")
        if report["findings"] or report.get('quality',{}).get('passed') is False or any(report["verification"][stage] == "failed" for stage in ("build", "runtime", "security")):
            raise click.exceptions.Exit(1)
        if verify and any(report["verification"][stage] == "not_verified" for stage in ("build", "runtime")):
            raise click.exceptions.Exit(2)
        if scout and report['verification']['security']=='not_verified':
            raise click.exceptions.Exit(2)
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error


def verify_project(project: Path, port: int, health_path: str, scan_image=False, env_file=None, network=None) -> dict:
    builder = DockerBuildValidator()
    if not builder.is_docker_available():
        return dict(build="not_verified", runtime="not_verified", reason="Docker daemon unavailable. Static checks do not prove runtime readiness.")
    tag = f"ebkit-doctor:{uuid4().hex}"
    runtime = None
    try:
        build = builder.build(project.resolve(), image_tag=tag, platform="linux/amd64", timeout=300, pull=True)
        if not build.success:
            return dict(build="failed", runtime="not_verified", reason="Docker build failed. Review the Dockerfile and run docker build locally for logs.")
        runtime = DockerRuntimeValidator(container_name=f"ebkit-doctor-{uuid4().hex}")
        result = runtime.validate(tag, port=port, health_check_path=health_path, bind_host="127.0.0.1", **({'env_file':env_file} if env_file else {}), **({'network':network} if network else {}))
        report = dict(build="passed", runtime="passed" if result.passed else "failed",
                    reason="HTTP probe passed." if result.passed else "Container startup or HTTP probe failed. Check required environment settings and startup configuration.")
        if scan_image:
            validator = DockerScoutValidator(max_critical=0,max_high=0)
            if not validator.is_scout_available():
                report.update(security='not_verified',securityReason='Docker Scout unavailable.')
            else:
                scan = validator.scan(tag)
                report.update(security='passed' if scan.gate_passed else 'failed',criticalCves=scan.critical_count,
                              highCves=scan.high_count,securityReason=scan.summary)
        return report
    finally:
        if runtime is not None:
            runtime._stop_container()
        try:
            subprocess.run(["docker", "image", "rm", tag], capture_output=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            pass
