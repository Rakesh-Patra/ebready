"""Additional Beanstalk operational controls shared by CLI and web."""
import json
import re
import time
from pathlib import Path

import click
from ebkit.commands import manage
from ebkit.commands.deploy import _poll_environment_health
from ebkit.deployment_state import save_deployment, load_deployments


def environment(eb, app, name, ready=False):
    rows=eb.describe_environments(ApplicationName=app,EnvironmentNames=[name],IncludeDeleted=False).get('Environments',[])
    if len(rows)!=1:raise click.ClickException('Environment was not found in this application.')
    if ready and rows[0].get('Status')!='Ready':raise click.ClickException('Wait for the environment to become Ready.')
    return rows[0]


def emit(value):click.echo(json.dumps(value,default=str))


@click.command('events')
@manage.target_options
@click.option('--follow',is_flag=True,help='Follow new events until Ctrl+C.')
@click.option('--interval',default=15,type=click.IntRange(5,300),show_default=True)
@click.option('--limit',default=100,type=click.IntRange(1,1000))
@click.option('--json','as_json',is_flag=True)
@manage.aws_errors
def events_command(app,environment_name,region,follow,interval,limit,as_json):
    """Read deployment events, optionally streaming new events."""
    session,app,name,_=manage.target(app,environment_name,region);eb=session.client('elasticbeanstalk')
    seen=set();start=None
    try:
        while True:
            args=dict(ApplicationName=app,EnvironmentName=name,MaxRecords=limit)
            if start:args['StartTime']=start
            rows=[];tokens=set()
            while True:
                page=eb.describe_events(**args);rows.extend(page.get('Events',[]))
                token=page.get('NextToken')
                if not follow or not token:break
                if token in tokens:raise RuntimeError('Repeated event pagination token.')
                tokens.add(token);args['NextToken']=token
            fresh=[]
            for row in sorted(rows,key=lambda item:str(item.get('EventDate',''))):
                key=(str(row.get('EventDate')),row.get('RequestId'),row.get('Message'))
                if key not in seen:fresh.append(row);seen.add(key)
            if as_json:emit({'environment':name,'events':fresh})
            else:
                for row in fresh:click.echo(f"{row.get('EventDate','')} {row.get('Severity','INFO')} {row.get('Message','')}")
                if not fresh and not follow:click.echo('No events found.')
            if not follow:return
            if rows:
                start=max((row.get('EventDate') for row in rows if row.get('EventDate')),default=start)
                seen={key for key in seen if key[0]==str(start)}
            time.sleep(interval)
    except KeyboardInterrupt:click.echo('Events stream stopped.')


@click.command('health')
@manage.target_options
@click.option('--refresh',is_flag=True,help='Refresh until Ctrl+C.')
@click.option('--interval',default=30,type=click.IntRange(10,300),show_default=True)
@click.option('--json','as_json',is_flag=True)
@manage.aws_errors
def health_command(app,environment_name,region,refresh,interval,as_json):
    """Show environment health causes, application metrics and latency."""
    session,app,name,_=manage.target(app,environment_name,region);eb=session.client('elasticbeanstalk')
    try:
        while True:
            environment(eb,app,name)
            report=eb.describe_environment_health(EnvironmentName=name,AttributeNames=['All'])
            if as_json:emit(report)
            else:
                click.echo(f"{name}: {report.get('HealthStatus','Unknown')} / {report.get('Color','Unknown')}")
                for cause in report.get('Causes',[]):click.echo(f'  {cause}')
                click.echo(json.dumps(report.get('ApplicationMetrics',{}),default=str))
            if not refresh:return
            time.sleep(interval)
    except KeyboardInterrupt:click.echo('Health refresh stopped.')


def option_key(value):
    parts=value.split('/',1)
    if len(parts)!=2 or not all(parts) or not re.fullmatch(r'[A-Za-z0-9_:.-]+',parts[0]) or not re.fullmatch(r'[-A-Za-z0-9_./: ]{1,255}',parts[1]):
        raise click.UsageError('Use NAMESPACE/OPTION for option names.')
    return dict(Namespace=parts[0],OptionName=parts[1])


@click.command('options')
@manage.target_options
@click.option('--file',type=click.Path(exists=True,dir_okay=False),help='JSON array of Namespace, OptionName and Value settings.')
@click.option('--remove',multiple=True,help='Remove an option: NAMESPACE/OPTION.')
@click.option('--json','as_json',is_flag=True)
@click.option('-y','--yes',is_flag=True)
@manage.aws_errors
def options_command(app,environment_name,region,file,remove,as_json,yes):
    """List option names or apply full environment options; values stay hidden."""
    session,app,name,_=manage.target(app,environment_name,region);eb=session.client('elasticbeanstalk')
    if not file and not remove:
        rows=[{key:row[key] for key in ('Namespace','OptionName') if key in row} for row in manage.settings(eb,app,name)]
        if as_json:emit({'environment':name,'options':rows})
        else:
            for row in rows:click.echo(f"{row['Namespace']}/{row['OptionName']} (value hidden)")
        return
    additions=[]
    if file:
        try:
            path=Path(file)
            if path.stat().st_size>65536:raise ValueError()
            additions=json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(additions,list) or len(additions)>100:raise ValueError()
            for row in additions:
                if not isinstance(row,dict) or set(row)!={'Namespace','OptionName','Value'} or not all(isinstance(value,str) for value in row.values()):raise ValueError()
                option_key(row['Namespace']+'/'+row['OptionName'])
                if len(row['Value'])>4096 or '\x00' in row['Value']:raise ValueError()
        except (ValueError,OSError):raise click.ClickException('Invalid options JSON. Values were not printed.') from None
    removals=[option_key(value) for value in remove]
    keys=[(row['Namespace'],row['OptionName']) for row in additions+removals]
    if len(set(keys))!=len(keys):raise click.UsageError('An option cannot be duplicated or both set and removed.')
    if any(namespace==manage.ENV_NAMESPACE and name in ('env-variables','service-port') for namespace,name in keys):
        raise click.UsageError('Use ebkit config for variables and ebkit deploy --port for the service port.')
    environment(eb,app,name,True)
    manage.confirm_change(name,'Apply environment option changes; charges and availability may change.',yes)
    args=dict(EnvironmentName=name)
    if additions:args['OptionSettings']=additions
    if removals:args['OptionsToRemove']=removals
    eb.update_environment(**args);click.echo('Environment option update requested.')


@click.command('config-save')
@manage.target_options
@click.option('--name',required=True)
@click.option('-y','--yes',is_flag=True)
@manage.aws_errors
def config_save_command(app,environment_name,region,name,yes):
    """Save an AWS configuration template (Standard Mode only)."""
    session,app,env,_=manage.target(app,environment_name,region);eb=session.client('elasticbeanstalk')
    row=environment(eb,app,env,True)
    if row.get('Tier',{}).get('Type')=='EKS':raise click.ClickException('AWS saved configuration templates support Standard Mode only. Use options for Cluster settings.')
    if not re.fullmatch(r'[A-Za-z0-9-]{1,100}',name):raise click.UsageError('Invalid template name.')
    manage.confirm_change(env,'Save environment configuration in AWS (including configured settings).',yes)
    eb.create_configuration_template(ApplicationName=app,TemplateName=name,EnvironmentId=row['EnvironmentId'])
    click.echo('Configuration template saved in AWS.')


@click.command('config-apply')
@manage.target_options
@click.option('--name',required=True)
@click.option('-y','--yes',is_flag=True)
@manage.aws_errors
def config_apply_command(app,environment_name,region,name,yes):
    """Apply an AWS configuration template (Standard Mode only)."""
    session,app,env,_=manage.target(app,environment_name,region);eb=session.client('elasticbeanstalk')
    row=environment(eb,app,env,True)
    if row.get('Tier',{}).get('Type')=='EKS':raise click.ClickException('AWS saved configuration templates support Standard Mode only. Use options for Cluster settings.')
    if not re.fullmatch(r'[A-Za-z0-9-]{1,100}',name):raise click.UsageError('Invalid template name.')
    manage.confirm_change(env,'Apply saved configuration; settings and charges can change.',yes)
    eb.update_environment(EnvironmentName=env,TemplateName=name);click.echo('Configuration template update requested.')


@click.command('version-delete')
@manage.target_options
@click.option('--version',required=True)
@click.option('-y','--yes',is_flag=True)
@manage.aws_errors
def version_delete_command(app,environment_name,region,version,yes):
    """Delete an unused application version; retained ECR images are not deleted."""
    session,app,env,_=manage.target(app,environment_name,region);eb=session.client('elasticbeanstalk')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',version):raise click.UsageError('Invalid version label.')
    args=dict(ApplicationName=app,IncludeDeleted=False);tokens=set()
    while True:
        page=eb.describe_environments(**args)
        if any(row.get('VersionLabel')==version for row in page.get('Environments',[])):
            raise click.ClickException('This version is deployed in an active environment. It cannot be deleted.')
        token=page.get('NextToken')
        if not token:break
        if token in tokens:raise RuntimeError('Repeated environment pagination token.')
        tokens.add(token);args['NextToken']=token
    if not eb.describe_application_versions(ApplicationName=app,VersionLabels=[version]).get('ApplicationVersions'):
        raise click.ClickException('Version not found.')
    manage.confirm_change(env,f'Delete unused version {version}. ECR images remain.',yes)
    eb.delete_application_version(ApplicationName=app,VersionLabel=version,DeleteSourceBundle=False)
    click.echo('Application version deleted.')


@click.command('swap')
@manage.target_options
@click.option('--destination',required=True,help='Another Ready environment in the same application.')
@click.option('-y','--yes',is_flag=True)
@manage.aws_errors
def swap_command(app,environment_name,region,destination,yes):
    """Swap two environment URLs for a blue/green deployment."""
    session,app,name,record=manage.target(app,environment_name,region);eb=session.client('elasticbeanstalk')
    account=record.get('account_id') or session.client('sts').get_caller_identity()['Account']
    if name==destination:raise click.UsageError('Choose two different environments.')
    left=environment(eb,app,name,True);right=environment(eb,app,destination,True)
    if not left.get('CNAME') or not right.get('CNAME'):raise click.ClickException('Both environments need a CNAME before swapping.')
    if left.get('Tier')!=right.get('Tier'):raise click.ClickException('Both environments must have the same tier.')
    manage.confirm_change(name,f'Swap its URL with {destination}; traffic will change destinations.',yes)
    eb.swap_environment_cnames(SourceEnvironmentName=name,DestinationEnvironmentName=destination)
    for record in load_deployments():
        if record.get('account_id')==account and record.get('application_name')==app and record.get('region')==session.region_name and record.get('environment_name') in (name,destination):
            # Wait for status refresh rather than presenting a stale URL as authoritative.
            record['url']=None;save_deployment(record)
    click.echo('URL swap requested. Run status for both environments after DNS propagation.')


@click.command('clone')
@manage.target_options
@click.option('--name',required=True,help='New environment name.')
@click.option('--max-replicas',type=click.IntRange(1,100),help='Override Cluster clone replica bounds.')
@click.option('--wait/--no-wait',default=True)
@click.option('-y','--yes',is_flag=True)
@manage.aws_errors
def clone_command(app,environment_name,region,name,max_replicas,wait,yes):
    """Create another environment with the current version and managed settings."""
    session,app,source,record=manage.target(app,environment_name,region);eb=session.client('elasticbeanstalk')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{2,39}',name) or name.endswith('-'):raise click.UsageError('Use a 3?40 character environment name starting with a letter.')
    row=environment(eb,app,source,True)
    if not row.get('VersionLabel'):raise click.ClickException('Source has no deployed version.')
    if eb.describe_environments(EnvironmentNames=[name],IncludeDeleted=False).get('Environments'):raise click.ClickException('Destination environment already exists.')
    options=manage.settings(eb,app,source)
    if max_replicas is not None:
        if row.get('Tier',{}).get('Type')!='EKS':raise click.UsageError('--max-replicas applies to Cluster clones only.')
        options=[item for item in options if not (item.get('Namespace')==manage.SCALE_NAMESPACE and item.get('OptionName') in ('min-replica','max-replica'))]
        options.extend([dict(Namespace=manage.SCALE_NAMESPACE,OptionName='min-replica',Value='1'),dict(Namespace=manage.SCALE_NAMESPACE,OptionName='max-replica',Value=str(max_replicas))])
    manage.confirm_change(source,f'Create billable clone {name}. External database contents are not copied.',yes)
    args=dict(ApplicationName=app,EnvironmentName=name,VersionLabel=row['VersionLabel'],Tier=row['Tier'],OptionSettings=options)
    if row.get('Tier',{}).get('Type')!='EKS':
        if row.get('PlatformArn'):args['PlatformArn']=row['PlatformArn']
        elif row.get('SolutionStackName'):args['SolutionStackName']=row['SolutionStackName']
        else:raise click.ClickException('Source platform metadata is missing.')
    if row.get('OperationsRole'):args['OperationsRole']=row['OperationsRole']
    session_account=session.client('sts').get_caller_identity()['Account']
    clone={key:value for key,value in record.items() if key in ('source_url','port','image_uri','codebuild_project','source_commit')}
    clone.update(account_id=session_account,region=region or session.region_name,application_name=app,environment_name=name,
                 version_label=row['VersionLabel'],status='CLONING',health='Grey',url=None,cloned_from=source)
    save_deployment(clone)
    result=eb.create_environment(**args)
    clone.update(status=result.get('Status','Launching'),health=result.get('Health','Grey'))
    save_deployment(clone)
    if wait:
        clone.update(_poll_environment_health(eb,app,name));save_deployment(clone)
        if clone['status']!='Ready' or clone['health']!='Green':raise RuntimeError('Cloned environment did not become Ready / Green.')
    click.echo(f'Clone {name}: {clone["status"]}')
