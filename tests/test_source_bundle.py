import json
import stat
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from click.testing import CliRunner
from botocore.exceptions import ClientError
from ebkit.source_bundle import extract_zip, make_zip
from ebkit.commands.deploy import deploy, _build_image_in_aws
from ebkit.commands.init import init_command
from ebkit.zip_deploy import upload_source
from tests.test_interactive_cli import _create_sample_config

def archive(tmp_path, entries):
    file=tmp_path/'project.zip'
    with zipfile.ZipFile(file,'w') as bundle:
        for name,text in entries.items():bundle.writestr(name,text)
    return file

def test_wrapped_zip_is_flattened_and_secrets_dependencies_are_excluded(tmp_path):
    file=archive(tmp_path,{'project-main/Dockerfile':'FROM node:22-alpine\nCMD ["node","app.js"]','project-main/app.js':'console.log("hello")','project-main/.env':'API_KEY=private','project-main/.env.example':'API_KEY=','project-main/node_modules/thing':'unused','project-main/.git/config':'secret'})
    root=extract_zip(file,tmp_path/'out')
    assert (root/'Dockerfile').is_file()
    assert not (root/'.env').exists() and not (root/'node_modules').exists()
    prepared=make_zip(root,tmp_path/'prepared.zip')
    with zipfile.ZipFile(prepared) as bundle: assert set(bundle.namelist())=={'Dockerfile','app.js','.env.example'}

@pytest.mark.parametrize('name',['../outside','/outside','C:/outside','dir\\outside','dir/../../outside'])
def test_zip_rejects_traversal(tmp_path,name):
    file=archive(tmp_path,{name:'bad'})
    if '\\' in name:
        file.write_bytes(file.read_bytes().replace(name.replace('\\','/').encode(),name.encode()))
    with pytest.raises(ValueError,match='unsafe'):extract_zip(file,tmp_path/'out')
    assert not (tmp_path.parent/'outside').exists()

def test_zip_rejects_symlinks_and_duplicate_casefold_paths(tmp_path):
    file=tmp_path/'bad.zip'
    with zipfile.ZipFile(file,'w') as bundle:
        info=zipfile.ZipInfo('link');info.external_attr=(stat.S_IFLNK|0o777)<<16;bundle.writestr(info,'/etc/passwd')
    with pytest.raises(ValueError,match='links'):extract_zip(file,tmp_path/'out')
    with zipfile.ZipFile(file,'w') as bundle:bundle.writestr('App.js','a');bundle.writestr('app.js','b')
    with pytest.raises(ValueError,match='duplicate'):extract_zip(file,tmp_path/'out')

def test_zip_rejects_bomb_and_nonempty_destination(tmp_path):
    file=tmp_path/'bomb.zip'
    with zipfile.ZipFile(file,'w',zipfile.ZIP_DEFLATED) as bundle:bundle.writestr('bomb.txt','A'*100000)
    with pytest.raises(ValueError,match='compressed'):extract_zip(file,tmp_path/'out')
    (tmp_path/'out'/'keep').write_text('keep')
    with pytest.raises(ValueError,match='empty'):extract_zip(file,tmp_path/'out')

def test_cli_zip_deployment_needs_explicit_review_even_with_yes(tmp_path):
    file=archive(tmp_path,{'Dockerfile':'FROM node:22-alpine\nCMD ["node","app.js"]','app.js':'console.log("hello")'})
    with patch('ebkit.commands.deploy._execute_deploy') as execute:
        result=CliRunner().invoke(deploy,[str(file),'--yes'])
        assert result.exit_code!=0 and '--reviewed' in result.output
        execute.assert_not_called()
        result=CliRunner().invoke(deploy,[str(file),'--yes','--reviewed'])
        assert result.exit_code==0,result.output
        assert execute.call_args.kwargs['archive_path'].endswith('source.zip')
        assert execute.call_args.kwargs['source_url'].startswith('zip:')

def test_cli_zip_missing_dockerfile_never_touches_aws(tmp_path):
    file=archive(tmp_path,{'app.js':'console.log("hello")'})
    with patch('ebkit.commands.deploy._execute_deploy') as execute:
        result=CliRunner().invoke(deploy,[str(file),'--yes','--reviewed'])
        assert 'init --zip' in result.output and result.exit_code!=0
        execute.assert_not_called()

def test_hosted_zip_init_does_not_request_a_user_gemini_key(tmp_path):
    file=archive(tmp_path,{'app.js':'console.log("hello")'})
    with patch('ebkit.hosted_init.hosted_init') as hosted,patch('ebkit.commands.init._check_gemini_key') as key:
        result=CliRunner().invoke(init_command,['--zip',str(file),'--service-url','https://ebkit.example'])
        assert result.exit_code==0,result.output
        hosted.assert_called_once();key.assert_not_called()

def test_local_zip_init_exports_a_root_dockerfile_without_building_locally(tmp_path):
    file=archive(tmp_path,{'sample/requirements.txt':'fastapi\nuvicorn\n','sample/app/main.py':'from fastapi import FastAPI\napp=FastAPI()\n'})
    analyzer=MagicMock();analyzer.analyze.return_value=_create_sample_config()
    with patch('ebkit.commands.init._check_gemini_key'),patch('ebkit.commands.init.get_analyzer',return_value=analyzer),patch('ebkit.commands.init.load_config',return_value=None),patch('ebkit.generator.docker_ai.DockerAIService.is_available',return_value=False),patch('ebkit.generator.docker_ai.DockerAIService._generate_multitier_with_gemini',return_value=(None,'Generation mocked; template used.')),patch('ebkit.validator.docker_validator.DockerBuildValidator.build') as build:
        result=CliRunner().invoke(init_command,['--zip',str(file),'--yes','--no-build','--no-runtime','--no-scout'])
    assert result.exit_code==0,result.output
    assert (tmp_path/'project-ebkit'/'Dockerfile').is_file()
    assert (tmp_path/'project-prepared.zip').is_file()
    with zipfile.ZipFile(tmp_path/'project-prepared.zip') as bundle:assert 'Dockerfile' in bundle.namelist() and '.env' not in bundle.namelist()
    assert 'SECURITY VALIDATION SKIPPED' in result.output
    build.assert_not_called()

def test_zip_dry_run_does_not_write_an_output_folder_or_prepared_zip(tmp_path):
    file=archive(tmp_path,{'requirements.txt':'fastapi\nuvicorn\n','app/main.py':'from fastapi import FastAPI\napp=FastAPI()\n'})
    analyzer=MagicMock();analyzer.analyze.return_value=_create_sample_config()
    with patch('ebkit.commands.init._check_gemini_key'),patch('ebkit.commands.init.get_analyzer',return_value=analyzer),patch('ebkit.commands.init.load_config',return_value=None):
        result=CliRunner().invoke(init_command,['--zip',str(file),'--output',str(tmp_path/'result'),'--yes','--dry-run'])
    assert result.exit_code==0,result.output
    assert not (tmp_path/'result').exists()
    assert not (tmp_path/'project-prepared.zip').exists()

def test_codebuild_zip_source_and_role_are_scoped_to_the_uploaded_object():
    iam,build=MagicMock(),MagicMock();build.start_build.return_value={'build':{'id':'build-one'}}
    with patch('ebkit.commands.deploy._ensure_iam_role',return_value='role'),patch('ebkit.commands.deploy._wait_for_codebuild'):
        _build_image_in_aws(iam,build,'123456789012','us-east-2','demo','demo','zip://source/project','123456789012.dkr.ecr.us-east-2.amazonaws.com/demo:v1',zip_source={'bucket':'private-source','key':'sources/a.zip','location':'private-source/sources/a.zip'})
    source=build.create_project.call_args.kwargs['source']
    assert source['type']=='S3' and source['location']=='private-source/sources/a.zip' and 'gitCloneDepth' not in source
    policy=json.loads(iam.put_role_policy.call_args.kwargs['PolicyDocument'])
    assert any(row['Resource']=='arn:aws:s3:::private-source/sources/a.zip' for row in policy['Statement'])

def test_zip_upload_blocks_public_access_encrypts_and_expires_sources(tmp_path):
    file=archive(tmp_path,{'app.js':'console.log("hello")'});s3=MagicMock()
    s3.head_bucket.side_effect=ClientError({'Error':{'Code':'404'}},'HeadBucket')
    record=upload_source(s3,file,'123456789012','us-east-2','demo')
    assert s3.put_public_access_block.call_args.kwargs['PublicAccessBlockConfiguration']['BlockPublicPolicy']
    assert s3.put_object.call_args.kwargs['ServerSideEncryption']=='AES256'
    assert record['key'].startswith('sources/')
    assert s3.put_bucket_lifecycle_configuration.call_args.kwargs['LifecycleConfiguration']['Rules'][0]['Expiration']['Days']==1

def test_zip_upload_does_not_take_over_an_existing_unverified_bucket(tmp_path):
    file=archive(tmp_path,{'app.js':'hello'});s3=MagicMock();s3.get_bucket_tagging.return_value={'TagSet':[]}
    with pytest.raises(RuntimeError,match='ownership'):upload_source(s3,file,'123456789012','us-east-2','demo')
    s3.put_object.assert_not_called();s3.put_bucket_tagging.assert_not_called()
