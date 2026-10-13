"""Use the operator's authenticated AI service without distributing its API key."""
import json
import os
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urlsplit
import click
from ebkit.source_bundle import extract_zip, MAX_ARCHIVE

def hosted_init(archive: str, output: str | None, service_url: str, port: int | None, skip_scout: bool = False) -> None:
    parsed = urlsplit(service_url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'localhost','127.0.0.1'})):
        raise click.ClickException('Use an HTTPS EBKit service URL (HTTP is allowed only on localhost).')
    token = os.environ.get('EBKIT_ACCESS_TOKEN')
    if not token:
        raise click.ClickException('Sign in on the EBKit website and copy its CLI session token into EBKIT_ACCESS_TOKEN. No Gemini key is needed.')
    file = Path(archive)
    if file.stat().st_size > MAX_ARCHIVE:
        raise click.ClickException('Use a ZIP no larger than 25 MB.')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs): return None
    opener = urllib.request.build_opener(NoRedirect)
    def request(route, payload, content_type='application/json', binary=False):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        req = urllib.request.Request(service_url.rstrip('/') + route, data=data, headers={'Content-Type':content_type,'Authorization':f'Bearer {token}'})
        try:
            with opener.open(req, timeout=420) as response:
                body=response.read(MAX_ARCHIVE + 1)
                if len(body)>MAX_ARCHIVE: raise click.ClickException('Service response exceeds the size limit.')
                return body if binary else json.loads(body)
        except urllib.error.HTTPError as error:
            raise click.ClickException(f'EBKit service rejected the request (HTTP {error.code}). Check your session and operator approval.') from None
        except urllib.error.URLError:
            raise click.ClickException('EBKit service could not be reached.') from None
        except TimeoutError:
            raise click.ClickException('Project preparation timed out. Try again later.') from None
    uploaded=request('/api/uploads',file.read_bytes(),'application/zip')
    prepared=request(f"/api/uploads/{uploaded['uploadId']}/prepare", {'port':port,'allowAI':True,'skipScout':skip_scout,'acknowledgeScoutBypass':skip_scout})
    provider={'gordon':'Docker AI (Gordon)','gemini':'Gemini','recipe':'EBKit reviewed distroless recipe','existing':'Existing Dockerfile'}.get(prepared.get('generator'),'AI')
    click.echo(f'\nDockerfile provider: {provider}\nReview the root Dockerfile:\n\n'+prepared['dockerfile'])
    click.echo('AI output is a draft; image build and runtime checks have not run.')
    scout=prepared.get('scout') or {}
    click.echo('\nDocker Scout — source dependencies (no image build)\n'+scout.get('summary','Security NOT VERIFIED.'))
    for finding in scout.get('details',[]):
        click.echo(f"  {finding.get('id','unknown')} [{finding.get('severity','unknown')}] | {', '.join(finding.get('packages',[]))} | fixed: {finding.get('fixed_version') or 'not reported'} | {', '.join(finding.get('paths',[]))}")
    if scout.get('bypassed'):
        click.echo('SECURITY VALIDATION SKIPPED: you bypassed Docker Scout. Security readiness is NOT VERIFIED; CVEs may remain.')
    elif not scout.get('gate_passed'):
        click.echo('PROJECT NOT READY: fix source dependency vulnerabilities and rerun init.')
        click.echo(f'If you explicitly choose to bypass Scout: ebkit init --zip "{archive}" --service-url "{service_url}" --no-scout')
        raise click.ClickException('Source security gate has not passed. Bypassing does not fix CVEs.')
    if not click.confirm('Save this Dockerfile and the prepared project ZIP?', default=False):
        raise click.Abort()
    target=Path(output) if output else file.with_name(file.stem+'-ebkit')
    if target.exists() and any(target.iterdir()): raise click.ClickException('Output directory must be empty.')
    prepared_zip=file.with_name(file.stem+'-prepared.zip')
    if prepared_zip.exists(): raise click.ClickException('Prepared ZIP already exists. Rename/remove it before retrying.')
    data=request(f"/api/uploads/{uploaded['uploadId']}/download", {'reviewed':True,'reviewHash':prepared['reviewHash']},binary=True)
    prepared_zip.write_bytes(data)
    extract_zip(prepared_zip,target)
    click.echo(f'Project: {target}\nPrepared ZIP: {prepared_zip}\nNo image build or runtime check was run. Scout checked source dependencies unless explicitly bypassed; the application image was not scanned.')
    click.echo(f'ebkit deploy "{prepared_zip}" --reviewed --port {prepared["port"]}')
