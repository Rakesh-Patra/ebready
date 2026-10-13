"""Watch a public GitHub default branch and update an existing deployment."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import boto3
import click
from botocore.exceptions import BotoCoreError, ClientError
from ebkit.deployment_state import get_state_path, load_deployments, save_deployment
from ebkit.commands.deploy import _check_aws_credentials, _execute_deploy


def canonical_source(source):
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", source.strip())
    if not match or match[2] in {".", ".."}:
        raise click.ClickException("Watch requires a public HTTPS GitHub repository URL; ZIPs are manual.")
    return f"https://github.com/{match[1]}/{match[2]}"


def github_head(source):
    api = "https://api.github.com/repos/" + source.removeprefix("https://github.com/")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "EBKit-Watch"}
    if os.environ.get("EBKIT_GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["EBKIT_GITHUB_TOKEN"]
    def get(url):
        with urlopen(Request(url, headers=headers), timeout=15) as response:
            data = response.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError("GitHub response exceeds the metadata limit.")
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError("Invalid GitHub metadata.")
            return value
    metadata = get(api)
    if metadata.get("private") is not False or metadata.get("archived") or not metadata.get("default_branch"):
        raise ValueError("Use an active public GitHub repository.")
    sha = get(f"{api}/commits/{quote(metadata['default_branch'], safe='')}").get("sha", "")
    if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("GitHub returned an invalid commit SHA.")
    dockerfile = get(f"{api}/contents/Dockerfile?ref={sha}")
    if dockerfile.get("type") != "file" or not 0 < dockerfile.get("size", 0) <= 262144:
        raise ValueError("The commit needs a nonempty root Dockerfile smaller than 256 KB.")
    return sha


def select_target(source, account, app=None, environment=None, region=None):
    records = [item for item in load_deployments()
               if item.get("account_id") == account
               and item.get("source_url", "").rstrip("/").removesuffix(".git").lower() == source.lower()
               and (not app or item.get("application_name") == app)
               and (not environment or item.get("environment_name") == environment)
               and (not region or item.get("region") == region)]
    if len(records) != 1:
        raise click.ClickException("Watch needs one existing saved deployment. Run ebkit deploy first, or select it with --app, --environment and --region.")
    record = records[0]
    if not all(record.get(key) for key in ("application_name", "environment_name", "region", "port")):
        raise click.ClickException("Saved deployment settings are incomplete. Deploy it manually first.")
    if str(record.get("status", "")).lower() in {"terminated", "terminating", "destroyed", "destroying"}:
        raise click.ClickException("This deployment is being removed or has been destroyed. Watch will not recreate it.")
    return record


@contextmanager
def watch_lock(target):
    identity = ':'.join(str(target[key]) for key in ('account_id', 'region', 'environment_name'))
    file = get_state_path().parent / ("watch-" + hashlib.sha256(identity.encode()).hexdigest()[:20] + ".lock")
    file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with file.open('x', encoding='utf-8') as output:
            output.write(str(os.getpid()))
    except FileExistsError:
        raise click.ClickException("Another watcher owns this target. Stop it first. If it crashed, remove its lock file: " + str(file)) from None
    try:
        yield
    finally:
        file.unlink(missing_ok=True)


def watch_loop(source, target, interval, once=False):
    last = target.get('watch_commit') or target.get('source_commit')
    eb = boto3.Session(region_name=target['region']).client('elasticbeanstalk')
    while True:
        current = select_target(source, target['account_id'], target['application_name'], target['environment_name'], target['region'])
        environments = eb.describe_environments(ApplicationName=target['application_name'], EnvironmentNames=[target['environment_name']], IncludeDeleted=False).get('Environments', [])
        if not environments or environments[0].get('Status') in ('Terminated', 'Terminating'):
            raise click.ClickException('The watched environment no longer exists. No replacement will be created.')
        if environments[0].get('Status') != 'Ready':
            click.echo('AWS is busy; waiting before checking for a new commit.')
        else:
            try:
                sha = github_head(source)
                if last is None:
                    current['watch_commit'] = sha
                    save_deployment(current)
                    last = sha
                    click.echo(f'Baseline {sha[:12]}. Watching for the next commit.')
                elif sha != last:
                    current['watch_commit'] = sha
                    save_deployment(current)
                    last = sha
                    click.echo(f'New commit {sha[:12]}; updating {target["environment_name"]}.')
                    _execute_deploy(source, target['application_name'], target['environment_name'], target['region'], target['port'], True, None, (), False, commit=sha, require_existing=True)
                elif once:
                    click.echo(f'No new commit ({sha[:12]}).')
            except (HTTPError, URLError, OSError, ValueError, RuntimeError, BotoCoreError, ClientError, click.ClickException) as error:
                click.echo(f'Watch check/deployment failed: {error}', err=True)
                if once:
                    raise click.ClickException('Watch check failed.') from None
        if once:
            return
        time.sleep(interval)


@click.command('watch')
@click.argument('source')
@click.option('--app', help='Select an existing saved application.')
@click.option('--environment', '--env-name', help='Select an existing saved environment.')
@click.option('--region', help='Select an existing saved AWS region.')
@click.option('--interval', default=300, show_default=True, type=click.IntRange(30, 3600), help='Seconds between checks.')
@click.option('--once', is_flag=True, help='Check once and exit.')
@click.option('-y', '--yes', is_flag=True, help='Authorize automatic updates without the startup confirmation.')
def watch_command(source, app, environment, region, interval, once, yes):
    """Automatically redeploy new GitHub commits to an existing environment.

    Keep this process running. ZIP updates remain manual. Ctrl+C stops watching.
    """
    source = canonical_source(source)
    authenticated, account, error = _check_aws_credentials()
    if not authenticated or not account:
        raise click.ClickException(f'AWS authentication required: {error}')
    target = select_target(source, account, app, environment, region)
    click.echo(f'Watch {source} ? {target["environment_name"]} ({target["region"]}), every {interval}s.')
    if not yes:
        click.confirm('Automatically build and deploy new commits to this existing AWS environment?', default=False, abort=True)
    try:
        with watch_lock(target):
            watch_loop(source, target, interval, once)
    except KeyboardInterrupt:
        click.echo('\nWatch stopped. The existing deployment remains running.')
    except (BotoCoreError, ClientError, OSError, ValueError) as error:
        raise click.ClickException(str(error)) from None
