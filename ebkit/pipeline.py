"""Generate a reviewable GitHub Actions/OIDC deployment bundle."""
import re
import shlex
import yaml
from ebkit.release import validate_policy


def generate_pipeline(app,env,region,repository,data):
    if not re.fullmatch(r'https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+',repository):raise ValueError('Pipeline requires a public GitHub project.')
    role=data.get('roleArn','');source=data.get('cliSource','Rakesh-Patra/ebready');ref=data.get('cliRef','main');gate=data.get('approvalEnvironment','production')
    if not re.fullmatch(r'arn:aws:iam::[0-9]{12}:role/[-A-Za-z0-9_./]+',role):raise ValueError('Provide the deployment OIDC role ARN.')
    if not re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+',source) or not re.fullmatch(r'[A-Za-z0-9_.-]+',ref) or not re.fullmatch(r'[A-Za-z0-9_-]+',gate):raise ValueError('Invalid pipeline source/ref or approval environment.')
    policy=validate_policy(data.get('releasePolicy',{'testCommands':[['npm','ci'],['npm','test']],'healthPath':'/health','autoRollback':True}))
    if not policy['testCommands']:raise ValueError('Configure application test commands before generating CI/CD.')
    tests='set -euo pipefail\n'+'\n'.join(shlex.join(command) for command in policy['testCommands'])
    account=role.split(':')[4];repo=repository.removeprefix('https://github.com/')
    subject=data.get('oidcSubject',f'repo:{repo}:environment:{gate}')
    if not isinstance(subject,str) or not subject.startswith('repo:') or '*' in subject or len(subject)>512:raise ValueError('Use the exact repository/environment OIDC subject, without wildcards.')
    workflow={'name':'EBKit verified release','on':{'push':{'branches':['main']},'pull_request':{'branches':['main']},'workflow_dispatch':{}},'permissions':{'contents':'read'},'concurrency':{'group':'ebkit-'+env,'cancel-in-progress':False},'jobs':{'test':{'runs-on':'ubuntu-latest','steps':[{'uses':'actions/checkout@v6','with':{'persist-credentials':False}},{'name':'Application tests','shell':'bash','run':tests}]},'deploy':{'if':"github.event_name != 'pull_request'",'needs':['test'],'runs-on':'ubuntu-latest','environment':gate,'permissions':{'contents':'read','id-token':'write'},'steps':[{'uses':'actions/checkout@v6','with':{'persist-credentials':False}},{'uses':'aws-actions/configure-aws-credentials@v6.3.0','with':{'role-to-assume':role,'aws-region':region,'allowed-account-ids':account}},{'name':'Install EBKit','env':{'EBKIT_SOURCE':source,'EBKIT_REF':ref},'run':'python -m pip install "git+https://github.com/$EBKIT_SOURCE@$EBKIT_REF"'},{'name':'Deploy pinned commit with verification','env':{'SOURCE':repository,'APP':app,'ENVIRONMENT':env,'REGION':region,'SHA':'${{ github.sha }}'},'run':'ebkit deploy "$SOURCE" --app "$APP" --environment "$ENVIRONMENT" --region "$REGION" --commit "$SHA" --release-policy .ebkit/release.json --yes'}]}}}
    trust={'Version':'2012-10-17','Statement':[{'Effect':'Allow','Principal':{'Federated':f'arn:aws:iam::{account}:oidc-provider/token.actions.githubusercontent.com'},'Action':'sts:AssumeRoleWithWebIdentity','Condition':{'StringEquals':{'token.actions.githubusercontent.com:aud':'sts.amazonaws.com','token.actions.githubusercontent.com:sub':subject}}}]}
    return {'workflow':yaml.safe_dump(workflow,sort_keys=False),'releasePolicy':policy,'oidcTrustPolicy':trust,'setup':['Commit the workflow to .github/workflows/ebkit.yml and the policy to .ebkit/release.json.','Create/enable the GitHub OIDC provider and attach the required deployment permissions to the chosen IAM role.','Configure required reviewers for the named GitHub environment. Merely naming an environment does not enable approval gates.','Verify the exact OIDC subject: newer GitHub repositories can include owner/repository IDs. Override oidcSubject before applying IAM trust.','Pin cliRef to a reviewed release or commit before production use. Disable the EBKit watcher for this project to avoid duplicate deployments and bypassing CI approvals.']}
