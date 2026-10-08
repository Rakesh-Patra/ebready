"""
EBKit CLI entry point.

Usage:
  ebkit init [OPTIONS]
  ebkit --help
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


@click.group()
@click.version_option(package_name="ebkit")
def cli() -> None:
    """
    EBKit — AI-powered deployment-kit generator for AWS Elastic Beanstalk.
    """


cli.add_command(init_command, name="init")
cli.add_command(deploy_command, name="deploy")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
