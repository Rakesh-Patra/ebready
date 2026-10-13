import json
from pathlib import Path

import pytest

from ebkit.generator.docker_ai import DockerAIService
from ebkit.generator.production_recipe import production_recipe
from ebkit.models.deployment_config import DeploymentConfig
from ebkit.validator.production import quality_report, require_quality
from ebkit.validator.build_inputs import check_build_inputs
from scripts.verify_production import samples, IGNORE


@pytest.mark.parametrize('name,settings,files',samples(),ids=[sample[0] for sample in samples()])
def test_six_reviewed_recipes_are_distroless_candidates_and_build_inputs_exist(tmp_path,name,settings,files):
    for filename,text in {**files,'.dockerignore':IGNORE}.items():
        (tmp_path / filename).write_text(text,encoding='utf-8')
    recipe=production_recipe(DeploymentConfig(**settings,port=8080),tmp_path)
    report=require_quality(recipe,8080,True)
    check_build_inputs(tmp_path,recipe)
    assert report['distroless']
    assert report['imageSecurity']=='not_verified'
    final=recipe.rsplit('\nFROM ',1)[1]
    assert '\nRUN ' not in final
    assert 'USER 65532:65532' in final
    assert 'Digest pinning' in [item['name'] for item in report['checks'] if not item['passed']]


GOOD='''FROM node:24-trixie-slim AS build
WORKDIR /app
COPY . .
FROM gcr.io/distroless/nodejs24-debian13:nonroot
WORKDIR /app
COPY --from=build /app /app
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/nodejs/bin/node"]
CMD ["server.js"]
'''


@pytest.mark.parametrize('old,new,reason',[
    ('USER 65532:65532','USER root','non-root'),
    ('USER 65532:65532','RUN useradd app || true\nUSER app','shell'),
    ('CMD ["server.js"]','CMD npm start','JSON array'),
    ('ENTRYPOINT ["/nodejs/bin/node"]','ENTRYPOINT ["sh", "-c"]','without a shell'),
    ('COPY --from=build /app /app','COPY --from=build /app /app 2>/dev/null || true','COPY/ADD'),
    ('COPY --from=build /app /app','COPY --from=build / /','entire builder'),
    ('node:24-trixie-slim','node:22-trixie-slim','versions'),
    ('node:24-trixie-slim','node:24-alpine','ABI'),
    ('node:24-trixie-slim','node:latest','versioned'),
    ('CMD ["server.js"]','CMD ["server.js", "--watch"]','Development'),
    ('EXPOSE 8080','EXPOSE 80 8080','port 8080'),
    ('COPY . .','COPY .env /app/.env','secret'),
    ('COPY . .','COPY . .\nENV API_KEY=actual-value','Credentials'),
    ('nodejs24-debian13','nodejs20-debian13','supported'),
])
def test_rejects_unsafe_or_incompatible_gordon_drafts(old,new,reason):
    report=quality_report(GOOD.replace(old,new),8080,True)
    assert not report['passed']
    assert reason.lower() in ' '.join(report['errors']).lower()


def test_builder_user_does_not_satisfy_final_nonroot():
    text=GOOD.replace('COPY . .','COPY . .\nUSER 65532:65532').replace('COPY --from=build /app /app\nUSER 65532:65532','COPY --from=build /app /app')
    assert not quality_report(text,8080,True)['passed']


def test_normalization_never_invents_image_tags_users_or_changes_ports():
    config=DeploymentConfig(language='node',start_command='node server.js')
    text='FROM node:latest\nEXPOSE 80\nCMD ["node","server.js"]'
    normalized=DockerAIService._ensure_cluster_mode_safety(text,config)
    assert normalized.strip()==text
    assert not quality_report(normalized,8080)['passed']


def test_python_native_abi_mismatch_is_rejected():
    text=GOOD.replace('node:24-trixie-slim','python:3.12-slim-bookworm').replace('nodejs24-debian13','python3-debian13').replace('/nodejs/bin/node','/usr/bin/python3').replace('server.js','app.py')
    report=quality_report(text,8080,True)
    assert not report['passed']
    assert any('3.13' in issue for issue in report['errors'])


def test_node_missing_lockfile_is_not_silently_resolved(tmp_path):
    (tmp_path/'package.json').write_text(json.dumps({'dependencies':{'express':'*'},'scripts':{'start':'node server.js'}}))
    (tmp_path/'server.js').write_text('console.log("hi")')
    assert production_recipe(DeploymentConfig(language='node',runtime_version='24',start_command='npm start'),tmp_path) is None


@pytest.mark.parametrize('payload',[
    {'runs':[]},
    {'runs':[{'tool':{'driver':{'rules':[]}}}]},
    {'runs':[{'tool':{'driver':{'rules':[{'id':'CVE-unknown','properties':{}}]}},'results':[{'ruleId':'CVE-unknown','ruleIndex':0}]}]},
    {'vulnerabilities':[{'id':'CVE-unknown','severity':'UNKNOWN'}]},
])
def test_scout_unknown_or_incomplete_reports_never_pass(monkeypatch,payload):
    from types import SimpleNamespace
    from ebkit.validator.docker_validator import DockerScoutValidator
    monkeypatch.setattr(DockerScoutValidator,'is_scout_available',lambda self:True)
    monkeypatch.setattr('ebkit.validator.docker_validator.subprocess.run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps(payload),stderr=''))
    report=DockerScoutValidator(max_high=0).scan('test:image')
    assert not report.gate_passed


def test_registry_digest_pins_skip_stage_aliases_and_never_invent_digests(monkeypatch):
    from types import SimpleNamespace
    from ebkit.generator.image_pins import pin_base_images
    digest='sha256:'+'a'*64
    calls=[]
    def resolve(args,**kwargs):
        calls.append(args[-1]);return SimpleNamespace(returncode=0,stdout=f'Name: image\nDigest: {digest}\n',stderr='')
    monkeypatch.setattr('ebkit.generator.image_pins.subprocess.run',resolve)
    result=pin_base_images(GOOD+'FROM build AS reuse\n')
    assert calls==['node:24-trixie-slim','gcr.io/distroless/nodejs24-debian13:nonroot']
    assert result.count('@'+digest)==2
    assert 'FROM build AS reuse' in result


def test_npm_ci_without_copied_lockfile_is_rejected_before_build(tmp_path):
    (tmp_path/'package.json').write_text('{"name":"sample"}')
    text='FROM node:24-trixie-slim AS build\nCOPY package.json ./\nRUN npm ci\n'
    with pytest.raises(ValueError,match='lockfile'):
        check_build_inputs(tmp_path,text)
    (tmp_path/'package-lock.json').write_text('{"lockfileVersion":3}')
    with pytest.raises(ValueError,match='lockfile'):
        check_build_inputs(tmp_path,text)
    check_build_inputs(tmp_path,text.replace('COPY package.json ./','COPY package*.json ./'))


def test_trusted_distroless_nonroot_default_is_made_explicit_in_final_stage_only():
    config=DeploymentConfig(language='node',runtime_version='24',start_command='node server.js')
    text=GOOD.replace('COPY . .','COPY . .\nUSER root').replace('COPY --from=build /app /app\nUSER 65532:65532','COPY --from=build /app /app')
    result=DockerAIService._ensure_cluster_mode_safety(text,config)
    final=result.rsplit('\nFROM ',1)[1]
    assert 'USER 65532:65532' in final
    assert 'useradd' not in result
    assert quality_report(result,8080,True)['passed']
