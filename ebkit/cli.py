"""
EBKit CLI entry point.

Usage:
  ebkit COMMAND --help
  ebkit init [OPTIONS]
  ebkit deploy GITHUB_URL [OPTIONS]
  ebkit status [OPTIONS]
"""

import sys
import os
import boto3
# pyrefly: ignore [missing-import]
import click

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from ebkit.commands.init import init_command
from ebkit.commands.doctor import doctor_command
from ebkit.commands.deploy import deploy_command
from ebkit.commands.watch import watch_command
from ebkit.commands.production_ops import operations_command
from ebkit.commands.controls import (events_command, health_command, options_command, config_save_command, config_apply_command, version_delete_command, swap_command, clone_command)
from ebkit.commands.operations import (
    destroy_command,
    diagnose_command,
    envlist_command,
    logs_command,
    status_command,
)
from ebkit.commands.manage import (
    scale_command, config_command, versions_command, rollback_command, cleanup_status_command, resources_command,
)


@click.group(
    epilog=(
        "Getting started: verify your AWS account with `aws sts get-caller-identity`, "
        "then run `ebkit deploy --help` or `ebkit init --help`. Deploy requires a "
        "public GitHub repository or reviewed project ZIP with a root Dockerfile. Commands that change AWS "
        "resources may incur charges; review the target and confirmation prompts. "
        "Run `ebkit COMMAND --help` for command-specific options."
    )
)
@click.option("--profile", help="AWS named credentials profile for this command.")
@click.pass_context
@click.version_option(package_name="ebkit")
def cli(ctx, profile=None) -> None:
    """
    EBKit — AI-powered deployment-kit generator for AWS Elastic Beanstalk.
    """
    if profile:
        previous = os.environ.get("AWS_PROFILE")
        previous_session = boto3.DEFAULT_SESSION
        boto3.DEFAULT_SESSION = None
        os.environ["AWS_PROFILE"] = profile
        def restore():
            boto3.DEFAULT_SESSION = previous_session
            if previous is None: os.environ.pop("AWS_PROFILE", None)
            else: os.environ["AWS_PROFILE"] = previous
        ctx.call_on_close(restore)



cli.add_command(init_command, name="init")
cli.add_command(doctor_command, name="doctor")
cli.add_command(deploy_command, name="deploy")
cli.add_command(watch_command, name="watch")
cli.add_command(status_command, name="status")
cli.add_command(logs_command, name="logs")
cli.add_command(diagnose_command, name="diagnose")
cli.add_command(envlist_command, name="envlist")
cli.add_command(destroy_command, name="destroy")
for command in (operations_command, events_command, health_command, options_command, config_save_command, config_apply_command, version_delete_command, swap_command, clone_command, scale_command, config_command, versions_command, rollback_command, cleanup_status_command, resources_command):
    cli.add_command(command)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
