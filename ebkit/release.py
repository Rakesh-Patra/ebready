"""Reviewed test gates and HTTP release verification."""
import json
import re
import shlex
import time
from pathlib import Path
from urllib.request import Request,urlopen,HTTPRedirectHandler,build_opener
from urllib.error import HTTPError,URLError
from urllib.parse import urlsplit
import yaml


def validate_policy(value):
    if not isinstance(value,dict) or set(value)-{'testCommands','healthPath','expectedText','autoRollback','attempts'}:
        raise ValueError('Invalid release policy fields.')
    commands=value.get('testCommands',[])
    if not isinstance(commands,list) or len(commands)>10:raise ValueError('Use at most 10 test commands.')
    for command in commands:
        if not isinstance(command,list) or not command or len(command)>30 or any(not isinstance(part,str) or not part or len(part)>2048 or any(char in part for char in '\r\n\x00') for part in command):raise ValueError('Test commands must be arrays of arguments.')
    path=value.get('healthPath','/health')
    if not isinstance(path,str) or not re.fullmatch(r'/[A-Za-z0-9_./-]*',path) or '..' in path or path.startswith('//'):raise ValueError('Use a safe application-relative health path.')
    expected=value.get('expectedText','')
    if not isinstance(expected,str) or len(expected)>1024:raise ValueError('Expected health response text is too long.')
    if not isinstance(value.get('autoRollback',False),bool):raise ValueError('autoRollback must be boolean.')
    attempts=value.get('attempts',6)
    if type(attempts)!=int or not 1<=attempts<=30:raise ValueError('Use 1?30 verification attempts.')
    return {'testCommands':commands,'healthPath':path,'expectedText':expected,'autoRollback':value.get('autoRollback',False),'attempts':attempts}


def read_policy(file):
    path=Path(file)
    if path.stat().st_size>32768:raise ValueError('Release policy exceeds 32 KB.')
    return validate_policy(json.loads(path.read_text(encoding='utf-8')))


def buildspec(policy=None):
    commands=[shlex.join(command) for command in (policy or {}).get('testCommands',[])]
    return yaml.safe_dump({'version':0.2,'phases':{'pre_build':{'commands':['aws ecr get-login-password --region "$AWS_DEFAULT_REGION" | docker login --username AWS --password-stdin "$EBKIT_ECR_REGISTRY"']},'build':{'commands':commands+['docker build --platform linux/amd64 -t "$EBKIT_IMAGE_URI" .','docker push "$EBKIT_IMAGE_URI"']}}},sort_keys=False)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def verify_http(cname,policy):
    # Probe only the AWS-returned Beanstalk hostname, never a browser-supplied URL.
    if not isinstance(cname,str) or not re.fullmatch(r'[A-Za-z0-9.-]+\.elasticbeanstalk\.com',cname):
        raise ValueError('AWS did not return a supported Beanstalk CNAME for verification.')
    url='https://'+cname+policy['healthPath']
    for attempt in range(policy['attempts']):
        try:
            with build_opener(NoRedirect).open(Request(url,headers={'User-Agent':'EBKit-Release-Check'}),timeout=10) as response:
                data=response.read(65537)
                if response.status==200 and len(data)<=65536 and policy['expectedText'] in data.decode('utf-8',errors='replace'):return True
        except (HTTPError,URLError,TimeoutError,OSError):pass
        if attempt+1<policy['attempts']:time.sleep(10)
    return False


def verify_or_recover(eb,app,env,version,previous,policy,poll):
    live=eb.describe_environments(ApplicationName=app,EnvironmentNames=[env],IncludeDeleted=False).get('Environments',[])
    valid=bool(live and live[0].get('Status')=='Ready' and live[0].get('Health')=='Green' and live[0].get('VersionLabel')==version)
    if valid:valid=verify_http(live[0].get('CNAME'),policy)
    if valid:return {'release_verification':'passed','recovery':'not_needed'}
    if policy['autoRollback'] and previous and previous!=version:
        # Never overwrite a newer release that another operator started.
        if not live or live[0].get('VersionLabel')!=version:raise RuntimeError('Release verification failed; a different version is now active, so recovery was not applied.')
        eb.update_environment(EnvironmentName=env,VersionLabel=previous)
        result=poll(eb,app,env)
        restored=eb.describe_environments(ApplicationName=app,EnvironmentNames=[env],IncludeDeleted=False).get('Environments',[])
        healthy=bool(restored and restored[0].get('VersionLabel')==previous and restored[0].get('Health')=='Green' and restored[0].get('Status')=='Ready')
        return {**result,'version_label':previous,'release_verification':'failed','recovery':'restored' if healthy else 'failed'}
    return {'release_verification':'failed','recovery':'unavailable' if not previous else 'disabled'}
