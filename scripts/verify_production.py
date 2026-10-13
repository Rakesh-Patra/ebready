"""Generate and verify six self-contained production samples. No AWS resources.

Run: python scripts/verify_production.py --output artifacts/production-check --live-gordon
The script only removes its own uniquely named containers/images and temporary exports.
"""
import argparse
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ebkit.models.deployment_config import DeploymentConfig
from ebkit.generator.production_recipe import production_recipe
from ebkit.generator.docker_ai import DockerAIService
from ebkit.validator.production import POLICY_PROMPT, quality_report
from ebkit.validator.build_inputs import check_build_inputs
from ebkit.validator.docker_validator import DockerBuildValidator, DockerRuntimeValidator, DockerScoutValidator

IGNORE = '.env\n.env.*\n.git\n.aws\n.ssh\nnode_modules\n__pycache__\n*.pem\n*.key\n'
NODE = "const http=require('node:http');http.createServer((req,res)=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:true}));}).listen(8080,'0.0.0.0');\n"
PYTHON = "from http.server import BaseHTTPRequestHandler, HTTPServer\nclass Handler(BaseHTTPRequestHandler):\n def do_GET(self):\n  self.send_response(200); self.end_headers(); self.wfile.write(b'healthy')\nHTTPServer(('0.0.0.0',8080),Handler).serve_forever()\n"
GO = 'package main\nimport("net/http";"fmt")\nfunc main(){http.HandleFunc("/",func(w http.ResponseWriter,r *http.Request){fmt.Fprint(w,"healthy")});http.ListenAndServe(":8080",nil)}\n'
JAVA = 'import com.sun.net.httpserver.HttpServer;\nimport java.net.InetSocketAddress;\npublic class App { public static void main(String[] args) throws Exception { var server=HttpServer.create(new InetSocketAddress("0.0.0.0",8080),0); server.createContext("/",exchange->{byte[] body="healthy".getBytes();exchange.sendResponseHeaders(200,body.length);exchange.getResponseBody().write(body);exchange.close();});server.start(); }}\n'


def samples():
    node_package = {'name':'ebkit-production-sample','version':'1.0.0','scripts':{'start':'node server.js'}}
    build_package = {**node_package,'scripts':{'start':'node dist/server.js','build':'node build.js'}}
    return [
        ('node22',dict(language='node',runtime_version='22',start_command='npm start',dependency_file='package.json',package_manager='npm',entrypoint='server.js'),{'package.json':json.dumps(node_package),'server.js':NODE}),
        ('node24-build',dict(language='node',runtime_version='24',start_command='npm start',dependency_file='package.json',package_manager='npm',entrypoint='dist/server.js'),{'package.json':json.dumps(build_package),'package-lock.json':json.dumps({'name':build_package['name'],'version':'1.0.0','lockfileVersion':3,'requires':True,'packages':{'':{'name':build_package['name'],'version':'1.0.0'}}}),'server.js':NODE,'build.js':"const fs=require('node:fs');fs.mkdirSync('dist',{recursive:true});fs.copyFileSync('server.js','dist/server.js');\n"}),
        ('python-http',dict(language='python',runtime_version='3.13',start_command='python app.py',entrypoint='app.py'),{'app.py':PYTHON}),
        ('python-fastapi',dict(language='python',runtime_version='3.13',start_command='uvicorn app:app --host 0.0.0.0 --port 8080',dependency_file='requirements.txt',package_manager='pip',entrypoint='app.py'),{'requirements.txt':'fastapi==0.143.0\nuvicorn==0.53.0\n','app.py':"from fastapi import FastAPI\napp=FastAPI()\n@app.get('/')\n@app.get('/health')\ndef health(): return {'ok':True}\n"}),
        ('go-http',dict(language='go',runtime_version='1.26',start_command='./server',dependency_file='go.mod',entrypoint='main.go'),{'go.mod':'module example.com/ebkit-production\n\ngo 1.26\n','main.go':GO}),
        ('java21-http',dict(language='java',runtime_version='21',start_command='java App',entrypoint='App.java'),{'App.java':JAVA}),
    ]


def run(args, timeout=60):
    result = subprocess.run(args,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout)[-3000:])
    return result.stdout


def pin(text, cache):
    for image in set(re.findall(r'(?m)^FROM\s+(\S+)',text)):
        if image not in cache:
            output=run(['docker','buildx','imagetools','inspect',image],timeout=60)
            match=re.search(r'^Digest:\s+(sha256:[a-f0-9]{64})',output,re.M)
            if not match:
                raise RuntimeError(f'Cannot resolve digest for {image}')
            cache[image]=match[1]
        text=re.sub(r'(?m)^(FROM\s+)'+re.escape(image)+r'(?=\s|$)',r'\g<1>'+image+'@'+cache[image],text)
    return text


def verify(root,text):
    tag='ebkit-production-test:'+uuid4().hex
    runtime=DockerRuntimeValidator(container_name='ebkit-production-test-'+uuid4().hex)
    export_name='ebkit-production-export-'+uuid4().hex
    export=root/'runtime-export.tar'
    report={'quality':quality_report(text,8080,True),'build':'not_verified','runtime':'not_verified','imageSecurity':'not_verified'}
    try:
        built=DockerBuildValidator().build(root,image_tag=tag,platform='linux/amd64',timeout=600,pull=True)
        (root/'build.log').write_text(built.output+'\n'+(built.error or ''),encoding='utf-8')
        report['build']='passed' if built.success else 'failed'
        if not built.success:return report
        image=json.loads(run(['docker','image','inspect',tag]))[0]
        report['imageId']=image['Id'];report['sizeBytes']=image['Size'];report['user']=image['Config']['User']
        result=runtime.validate(tag,port=8080,health_check_path='/health',bind_host='127.0.0.1')
        report['runtime']='passed' if result.passed else 'failed'
        report['httpStatus']=result.status_code
        if not result.passed:report['runtimeError']=result.error
        run(['docker','create','--name',export_name,tag])
        run(['docker','export','--output',str(export),export_name],timeout=90)
        with tarfile.open(export) as archive:
            names={member.name.lstrip('./') for member in archive}
        forbidden=['bin/sh','bin/bash','usr/bin/sh','usr/bin/bash','usr/bin/npm','usr/local/bin/npm','usr/bin/pip','usr/local/bin/pip','app/.env','app/.aws/credentials']
        report['shellless']=not any(name in names for name in forbidden)
        report['nonRoot']=report['user'].split(':')[0] not in {'','0','root'}
        scan=DockerScoutValidator(max_critical=0,max_high=0).scan(tag,timeout=600)
        report['imageSecurity']='passed' if scan.gate_passed else 'failed'
        report['cves']={key:getattr(scan,key) for key in ('critical_count','high_count','medium_count','low_count')}
        report['securityReason']=scan.gate_reason or scan.summary
        report['findings']=scan.details
        return report
    finally:
        runtime._stop_container()
        subprocess.run(['docker','rm','-f',export_name],capture_output=True,timeout=20)
        export.unlink(missing_ok=True)
        subprocess.run(['docker','image','rm',tag],capture_output=True,timeout=30)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--generate-only',action='store_true');parser.add_argument('--live-gordon',action='store_true');parser.add_argument('--only',choices=[row[0] for row in samples()]);args=parser.parse_args()
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    results=[];cache={}
    previous=output/'report.json'
    if args.only and previous.exists():
        saved=json.loads(previous.read_text(encoding='utf-8'));results=[item for item in saved['samples'] if item['sample']!=args.only];cache=saved['baseDigests']
    for name,settings,files in samples():
        if args.only and name!=args.only:continue
        root=output/name;root.mkdir(exist_ok=True)
        for filename,content in {**files,'.dockerignore':IGNORE}.items():
            (root/filename).write_text(content,encoding='utf-8')
        text=production_recipe(DeploymentConfig(**settings,port=8080),root)
        if not text:raise RuntimeError(f'No reviewed recipe for {name}')
        if not args.generate_only:text=pin(text,cache)
        check_build_inputs(root,text)
        (root/'Dockerfile').write_text(text,encoding='utf-8')
        if args.generate_only:report={'quality':quality_report(text,8080,True),'build':'not_verified','runtime':'not_verified','imageSecurity':'not_verified'}
        else:
            try:report=verify(root,text)
            except Exception as error:report={'error':str(error),'build':'failed','runtime':'not_verified','imageSecurity':'not_verified'}
        results.append({'sample':name,**report})
        (root/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        (output/'report.json').write_text(json.dumps({'samples':results,'baseDigests':cache},indent=2),encoding='utf-8')
        print(json.dumps({'sample':name,'build':report['build'],'runtime':report['runtime'],'imageSecurity':report['imageSecurity'],'cves':report.get('cves')}),flush=True)
    if args.live_gordon:
        service=DockerAIService()
        root=output/'gordon-node22';root.mkdir(exist_ok=True);text=None;errors=[]
        (root/'package.json').write_text(json.dumps({'name':'ebkit-gordon','version':'1.0.0'}),encoding='utf-8');(root/'server.js').write_text(NODE,encoding='utf-8');(root/'.dockerignore').write_text(IGNORE,encoding='utf-8')
        prompt=POLICY_PROMPT+'\nGenerate a Node 22 Dockerfile. Root package.json has no dependencies AND NO LOCKFILE. Do not run npm ci or npm install; this application only uses the Node standard library. Root server.js uses node:http and listens on 0.0.0.0:8080. COPY package.json and server.js; use matching Debian trixie build and distroless Node 22 runtime. Return a Dockerfile code block only.'
        for attempt in range(2):
            try:
                text=service.extract_dockerfile(service.ask_gordon(prompt,timeout=90))
                if text:text=service._ensure_cluster_mode_safety(text,DeploymentConfig(language='node',runtime_version='22',start_command='node server.js',port=8080))
                report=quality_report(text or '',8080,True)
                errors=report['errors']
                if report['passed']:check_build_inputs(root,text);break
                prompt+='\nCorrect these rejected draft findings: '+' '.join(errors)
                text=None
            except Exception as error:errors=[str(error)];text=None;prompt+='\nCorrect this rejected draft: '+str(error)
        gordon={'qualityPassed':text is not None,'errors':errors}
        if text:
            text=pin(text,cache);(root/'Dockerfile').write_text(text,encoding='utf-8');gordon.update(verify(root,text))
        (output/'gordon.json').write_text(json.dumps(gordon,indent=2),encoding='utf-8')
        print(json.dumps({'gordon':gordon.get('qualityPassed'),'build':gordon.get('build'),'runtime':gordon.get('runtime'),'imageSecurity':gordon.get('imageSecurity')}),flush=True)
    if not args.generate_only and any(item.get('build')!='passed' or item.get('runtime')!='passed' or item.get('imageSecurity')!='passed' or not item.get('shellless') or not item.get('nonRoot') for item in results):return 1
    return 0


if __name__=='__main__':sys.exit(main())
