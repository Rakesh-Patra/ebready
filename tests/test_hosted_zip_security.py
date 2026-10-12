import json
from unittest.mock import patch
import pytest
import click
from ebkit.hosted_init import hosted_init

class Response:
    def __init__(self,payload):self.payload=payload
    def __enter__(self):return self
    def __exit__(self,*_args):pass
    def read(self,_limit):return json.dumps(self.payload).encode()

def test_hosted_init_scout_failure_stops_before_review_download_and_shows_bypass_guidance(tmp_path,monkeypatch,capsys):
    source=tmp_path/'project.zip';source.write_bytes(b'zip')
    monkeypatch.setenv('EBKIT_ACCESS_TOKEN','account-session')
    requests=[]
    def request(req,timeout):
        requests.append(req)
        if req.full_url.endswith('/api/uploads'):return Response({'uploadId':'upload'})
        return Response({'dockerfile':'FROM node:22-alpine\nCMD ["node","app.js"]','generator':'gordon','reviewHash':'hash','port':3000,'scout':{'gate_passed':False,'summary':'1 Critical, 7 High','details':[{'id':'CVE-test','severity':'critical','packages':['sample'],'fixed_version':'2.0','paths':['package.json']}]}})
    with patch('ebkit.hosted_init.urllib.request.build_opener') as opener,patch('ebkit.hosted_init.click.confirm') as confirm:
        opener.return_value.open.side_effect=request
        with pytest.raises(click.ClickException,match='security gate'):hosted_init(str(source),None,'https://ebkit.example',3000)
    output=capsys.readouterr().out
    assert 'CVE-test' in output and 'fixed: 2.0' in output and '--no-scout' in output
    confirm.assert_not_called();assert len(requests)==2
    assert not (tmp_path/'project-prepared.zip').exists()
    body=json.loads(requests[1].data);assert body['skipScout'] is False and body['acknowledgeScoutBypass'] is False

def test_hosted_init_explicit_scout_bypass_is_sent_and_warned(tmp_path,monkeypatch,capsys):
    source=tmp_path/'project.zip';source.write_bytes(b'zip')
    monkeypatch.setenv('EBKIT_ACCESS_TOKEN','account-session')
    requests=[]
    def request(req,timeout):
        requests.append(req)
        return Response({'uploadId':'upload'}) if len(requests)==1 else Response({'dockerfile':'FROM node:22-alpine\nCMD ["node","app.js"]','generator':'gordon','reviewHash':'hash','port':3000,'scout':{'bypassed':True,'gate_passed':False,'summary':'NOT VERIFIED','details':[]}})
    with patch('ebkit.hosted_init.urllib.request.build_opener') as opener,patch('ebkit.hosted_init.click.confirm',return_value=False):
        opener.return_value.open.side_effect=request
        with pytest.raises(click.Abort):hosted_init(str(source),None,'https://ebkit.example',3000,skip_scout=True)
    body=json.loads(requests[1].data)
    assert body['skipScout'] is True and body['acknowledgeScoutBypass'] is True
    assert 'SECURITY VALIDATION SKIPPED' in capsys.readouterr().out
