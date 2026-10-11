"""
EBKit CLI entry point.

Usage:
  ebkit COMMAND --help
  ebkit init [OPTIONS]
  ebkit deploy GITHUB_URL [OPTIONS]
  ebkit status [OPTIONS]
"""

import sys
# pyrefly: ignore [missing-import]
import click

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from ebkit.commands.init import init_command
from ebkit.commands.deploy import deploy_command
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
        "public GitHub repository with a root Dockerfile. Commands that change AWS "
        "resources may incur charges; review the target and confirmation prompts. "
        "Run `ebkit COMMAND --help` for command-specific options."
    )
)
@click.version_option(package_name="ebkit")
def cli() -> None:
    """
    EBKit — AI-powered deployment-kit generator for AWS Elastic Beanstalk.
    """


cli.add_command(init_command, name="init")
cli.add_command(deploy_command, name="deploy")
cli.add_command(status_command, name="status")
cli.add_command(logs_command, name="logs")
cli.add_command(diagnose_command, name="diagnose")
cli.add_command(envlist_command, name="envlist")
cli.add_command(destroy_command, name="destroy")
for command in (scale_command, config_command, versions_command, rollback_command, cleanup_status_command, resources_command):
    cli.add_command(command)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
