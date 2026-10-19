"""RDS, monitoring, scaling and billing integrations for owned environments."""
import hashlib
import json
import re
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.parse import quote

import click
from botocore.exceptions import ClientError
from ebkit.commands import manage,operations as deployment_ops
from ebkit.commands.controls import environment,emit
from ebkit.aws_resources import environment_resources
from ebkit.deployment_state import save_deployment

ACTIONS=['metrics','alarm','autoscale','cost','budget','leftovers','database-create','database-status','database-network','database-attach','database-check','database-migrate','database-snapshot','database-delete','pipeline']
READ_ACTIONS={'metrics','cost','leftovers','database-status','database-check','pipeline'}


def migration_has_transaction_control(sql):
    # Procedural BEGIN/END inside dollar-quoted function bodies are not
    # transaction commands. PostgreSQL still executes the entire migration
    # inside the connection's transaction.
    code = re.sub(r"\$(?P<tag>[A-Za-z_][A-Za-z0-9_]*|)\$.*?\$(?P=tag)\$|'(?:''|[^'])*'|--[^\n]*|/\*.*?\*/",
                  ' ', sql, flags=re.DOTALL)
    return bool(re.search(r'\b(?:COMMIT|ROLLBACK|BEGIN|END|SAVEPOINT|VACUUM)\b', code, re.IGNORECASE))


def config_file(file):
    if not file:return {}
    path=Path(file)
    try:
        if path.stat().st_size>1048576:raise ValueError()
        data=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data,dict):raise ValueError()
        return data
    except (ValueError,OSError):raise click.ClickException('Invalid operations JSON file; no values were printed.') from None


def number(data,key,default,low,high):
    value=data.get(key,default)
    if type(value) not in (int,float) or not low<=value<=high:raise click.UsageError(f'{key} must be between {low} and {high}.')
    return value


def owner_tags(app,env):
    return [{'Key':'ebkit:application','Value':app},{'Key':'elasticbeanstalk:environment-name','Value':env}]


def metric_queries(session,eb,env):
    dims=[{'Name':'namespace','Value':'eb-'+env}]
    def metric(id,name,namespace,dimensions,stat='Average',label=None):
        return {'Id':id,'Label':label or name,'MetricStat':{'Metric':{'Namespace':namespace,'MetricName':name,'Dimensions':dimensions},'Period':300,'Stat':stat},'ReturnData':True}
    cpu=metric('counter','container_cpu_usage_seconds_total','ElasticBeanstalk/Infrastructure',dims);cpu['ReturnData']=False
    queries=[cpu,{'Id':'cpu','Label':'CPU cores (rate)','Expression':'IF(RATE(counter)>=0,RATE(counter))','ReturnData':True},metric('memory','container_memory_working_set_bytes','ElasticBeanstalk/Infrastructure',dims,label='Memory bytes'),metric('replicas','EnvironmentReplicas','ElasticBeanstalk/Infrastructure',dims,label='Ready replicas')]
    resources=environment_resources(eb,env,session.region_name)
    for item in resources.get('LoadBalancers',[]):
        name=item.get('Name','')
        try:
            args={'LoadBalancerArns':[name]} if name.startswith('arn:') else {'Names':[name]}
            rows=session.client('elbv2').describe_load_balancers(**args).get('LoadBalancers',[])
            alb=next((row for row in rows if row.get('Type')=='application'),None)
            if not alb:continue
            dimensions=[{'Name':'LoadBalancer','Value':alb['LoadBalancerArn'].split('loadbalancer/',1)[1]}]
            queries.extend([metric('requests','RequestCount','AWS/ApplicationELB',dimensions,'Sum','Requests'),metric('errors','HTTPCode_Target_5XX_Count','AWS/ApplicationELB',dimensions,'Sum','Target 5xx errors'),metric('latency','TargetResponseTime','AWS/ApplicationELB',dimensions,'p95','p95 latency seconds')]);break
        except ClientError as error:
            if error.response.get('Error',{}).get('Code')=='LoadBalancerNotFound':continue
            raise
    return queries


def monitoring(session,eb,env,data):
    hours=number(data,'hours',24,1,168);end=datetime.now(timezone.utc);queries=metric_queries(session,eb,env)
    args=dict(MetricDataQueries=queries,StartTime=end-timedelta(hours=hours),EndTime=end,ScanBy='TimestampAscending',MaxDatapoints=10000)
    series={};tokens=set()
    while True:
        page=session.client('cloudwatch').get_metric_data(**args)
        for result in page.get('MetricDataResults',[]):
            key=result['Id'];row=series.setdefault(key,{'id':key,'label':result.get('Label',key),'points':[],'status':result.get('StatusCode')})
            row['points'].extend([{'time':timestamp.isoformat(),'value':value} for timestamp,value in zip(result.get('Timestamps',[]),result.get('Values',[]))])
            if result.get('StatusCode') not in ('Complete','PartialData'):raise RuntimeError('CloudWatch metric retrieval failed.')
        token=page.get('NextToken')
        if not token:break
        if token in tokens or len(tokens)>=20:raise RuntimeError('Metric pagination limit reached.')
        tokens.add(token);args['NextToken']=token
    return {'environment':env,'hours':hours,'series':list(series.values()),'note':'Missing samples are not zero. CPU is counter rate in cores; negative rates from container counter resets are omitted. Request/error/latency data require an Application Load Balancer.'}


def alarm(session,eb,app,env,data):
    selected=data.get('metric','errors');queries=metric_queries(session,eb,env)
    query=next((item for item in queries if item['Id']==selected),None)
    if not query or selected=='counter':raise click.UsageError('Choose an available cpu, memory, replicas, requests, errors or latency metric.')
    threshold=number(data,'threshold',1,0,10**12)
    topic=data.get('topicArn','');account=session.client('sts').get_caller_identity()['Account']
    if not re.fullmatch(rf'arn:aws:sns:{re.escape(session.region_name)}:{account}:[A-Za-z0-9_-]+',topic):raise click.UsageError('Use an SNS topic ARN in this AWS account and region.')
    alarm_queries=[dict(item,ReturnData=item['Id']==selected) for item in queries if item['Id']==selected or (selected=='cpu' and item['Id']=='counter')]
    name='ebkit-'+hashlib.sha256((app+':'+env+':'+selected).encode()).hexdigest()[:24]
    session.client('cloudwatch').put_metric_alarm(AlarmName=name,AlarmDescription=f'EBKit {env}: {selected}',Metrics=alarm_queries,Threshold=threshold,ComparisonOperator='GreaterThanThreshold',EvaluationPeriods=3,DatapointsToAlarm=2,TreatMissingData='missing',AlarmActions=[topic],OKActions=[topic],Tags=owner_tags(app,env))
    return {'alarm':name,'metric':selected,'threshold':threshold,'delivery':'SNS topic subscriptions must be confirmed.'}


def autoscale(eb,app,env,data):
    manage.cluster_only(eb,env)
    low=int(number(data,'minimum',1,1,100));high=int(number(data,'maximum',5,1,100))
    if low>high:raise click.UsageError('Minimum exceeds maximum.')
    options=[dict(Namespace=manage.SCALE_NAMESPACE,OptionName='min-replica',Value=str(low)),dict(Namespace=manage.SCALE_NAMESPACE,OptionName='max-replica',Value=str(high))]
    for metric in ('cpu','memory'):
        if metric+'Percent' in data:
            target=number(data,metric+'Percent',70,1,100)
            options.extend([dict(Namespace=manage.SCALE_NAMESPACE+':trigger',OptionName=metric+'-metric-type',Value='Utilization'),dict(Namespace=manage.SCALE_NAMESPACE+':trigger',OptionName=metric+'-value',Value=str(target))])
    if not any(key in data for key in ('cpuPercent','memoryPercent')):raise click.UsageError('Set cpuPercent or memoryPercent.')
    eb.update_environment(EnvironmentName=env,OptionSettings=options)
    events=eb.describe_events(EnvironmentName=env,MaxRecords=20).get('Events',[])
    return {'environment':env,'minimum':low,'maximum':high,'policy':data,'recentEvents':events,'explanation':'Targets are percentages of reserved CPU/memory. When multiple triggers apply, the highest required replica count wins.'}


def cost_report(session,env,data):
    days=int(number(data,'days',7,1,90));end=datetime.now(timezone.utc).date();start=end-timedelta(days=days)
    client=session.client('ce',region_name='us-east-1')
    cost_filter={'Tags':{'Key':'elasticbeanstalk:environment-name','Values':[env]}}
    response=client.get_cost_and_usage(TimePeriod={'Start':str(start),'End':str(end)},Granularity='DAILY',Metrics=['UnblendedCost'],Filter=cost_filter,GroupBy=[{'Type':'DIMENSION','Key':'SERVICE'}])
    periods=response.get('ResultsByTime',[]);total=sum(float(group['Metrics']['UnblendedCost']['Amount']) for period in periods for group in period.get('Groups',[]))
    has_data=any(period.get('Groups') for period in periods)
    next_month=(end.replace(day=28)+timedelta(days=4)).replace(day=1)
    forecast_start=end+timedelta(days=1)
    forecast_period={'start':str(forecast_start),'end':str(next_month)}
    if forecast_start>=next_month:
        forecast_usd=None
        forecast_status='unavailable'
    else:
        try:
            forecast=client.get_cost_forecast(TimePeriod={'Start':str(forecast_start),'End':str(next_month)},Metric='UNBLENDED_COST',Granularity='MONTHLY',Filter=cost_filter)
            forecast_usd=float(forecast['Total']['Amount'])
            forecast_status='available'
        except ClientError as error:
            if error.response.get('Error',{}).get('Code')!='DataUnavailableException':raise
            forecast_usd=None
            forecast_status='unavailable'
    return {'environment':env,'period':{'start':str(start),'end':str(end)},'daily':periods,'taggedCostUsd':total if has_data else None,'monthlyRunRateUsd':round(total/days*30,2) if has_data else None,'remainingMonthForecastUsd':forecast_usd,'forecastPeriod':forecast_period,'forecastStatus':forecast_status,'coverage':'Environment-tagged costs only; shared, untagged and recently incurred charges may be missing. Activate this cost allocation tag. Monthly run rate extrapolates past spend; the remaining-month forecast is AWS Cost Explorer’s estimate.'}


def budget(session,app,env,data):
    amount=number(data,'monthlyUsd',10,.01,1000000);email=data.get('email','')
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise click.UsageError('Provide a notification email address.')
    account=session.client('sts').get_caller_identity()['Account'];name='ebkit-'+hashlib.sha256((app+':'+env).encode()).hexdigest()[:24]
    session.client('budgets',region_name='us-east-1').create_budget(AccountId=account,Budget={'BudgetName':name,'BudgetType':'COST','TimeUnit':'MONTHLY','BudgetLimit':{'Amount':str(amount),'Unit':'USD'},'CostFilters':{'TagKeyValue':['elasticbeanstalk:environment-name$'+env]}},NotificationsWithSubscribers=[{'Notification':{'NotificationType':'ACTUAL','ComparisonOperator':'GREATER_THAN','Threshold':80,'ThresholdType':'PERCENTAGE'},'Subscribers':[{'SubscriptionType':'EMAIL','Address':email}]}])
    return {'budget':name,'monthlyUsd':amount,'coverage':'Environment-tagged costs only; budget alerts do not stop resources or spending.'}


def leftovers(session,eb,app,env):
    items=[];seen=set()
    for key in ('ebkit:application','EBKitApplication'):
        args={'TagFilters':[{'Key':key,'Values':[app]}],'ResourcesPerPage':100};tokens=set()
        while True:
            page=session.client('resourcegroupstaggingapi').get_resources(**args)
            for item in page.get('ResourceTagMappingList',[]):
                if item['ResourceARN'] not in seen:
                    seen.add(item['ResourceARN']);items.append({'arn':item['ResourceARN'],'tags':item.get('Tags',[])})
            token=page.get('PaginationToken')
            if not token:break
            if token in tokens or len(tokens)>=20:raise RuntimeError('Resource inventory pagination limit reached.')
            tokens.add(token);args['PaginationToken']=token
    live=eb.describe_environments(ApplicationName=app,EnvironmentNames=[env],IncludeDeleted=False).get('Environments',[])
    return {'environment':env,'environmentExists':bool(live),'resources':items,'coverage':'Tagged application resources only. RDS and stored images can remain billable after environment teardown; retained resources are not automatically deleted.'}


def db_id(app):return 'ebkit-'+hashlib.sha256(app.encode()).hexdigest()[:20]


def owned_database(session,app):
    rds=session.client('rds');rows=rds.describe_db_instances(DBInstanceIdentifier=db_id(app)).get('DBInstances',[])
    if not rows:raise click.ClickException('No managed database exists for this application.')
    row=rows[0];tags={item['Key']:item['Value'] for item in rds.list_tags_for_resource(ResourceName=row['DBInstanceArn']).get('TagList',[])}
    if tags.get('ebkit:application')!=app:raise click.ClickException('Database ownership tags do not match this application.')
    return row


def database_create(session,eb,app,env,record,data):
    record = dict(record)
    ec2=session.client('ec2');rds=session.client('rds');vpc=data.get('vpcId');subnets=data.get('subnetIds',[]);source=data.get('applicationSecurityGroup')
    if not isinstance(vpc,str) or not re.fullmatch(r'vpc-[a-f0-9]+',vpc) or not isinstance(subnets,list) or not 2<=len(subnets)<=16 or any(not isinstance(item,str) or not re.fullmatch(r'subnet-[a-f0-9]+',item) for item in subnets) or len(set(subnets))!=len(subnets) or not isinstance(source,str) or not re.fullmatch(r'sg-[a-f0-9]+',source):raise click.UsageError('Provide vpcId, at least two subnetIds and applicationSecurityGroup.')
    rows=ec2.describe_subnets(SubnetIds=subnets).get('Subnets',[])
    if len(rows)!=len(subnets) or any(row['VpcId']!=vpc for row in rows) or len({row['AvailabilityZone'] for row in rows})<2:raise click.UsageError('Database subnets must belong to this VPC and span two availability zones.')
    group=ec2.describe_security_groups(GroupIds=[source]).get('SecurityGroups',[])
    if len(group)!=1 or group[0]['VpcId']!=vpc:raise click.UsageError('Application security group must belong to the same VPC.')
    size=number(data,'storageGb',20,20,1000);instance=data.get('instanceClass','db.t4g.micro')
    if type(size) is not int:raise click.UsageError('storageGb must be an integer.')
    if not isinstance(instance,str) or not re.fullmatch(r'db\.[a-z0-9]+\.[a-z0-9]+',instance):raise click.UsageError('Invalid RDS instance class.')
    if type(data.get('multiAz',False)) is not bool:raise click.UsageError('multiAz must be true or false.')
    record.update(application_name=app, environment_name=env, region=session.region_name,
                  account_id=session.client('sts').get_caller_identity()['Account'])
    identifier=db_id(app);tags=owner_tags(app,env)
    try:sg=ec2.create_security_group(GroupName=identifier,Description='Private EBKit PostgreSQL access',VpcId=vpc,TagSpecifications=[{'ResourceType':'security-group','Tags':tags}])['GroupId']
    except ClientError as error:
        if error.response.get('Error',{}).get('Code')!='InvalidGroup.Duplicate':raise
        groups=ec2.describe_security_groups(Filters=[{'Name':'vpc-id','Values':[vpc]},{'Name':'group-name','Values':[identifier]}]).get('SecurityGroups',[])
        if len(groups)!=1 or {item['Key']:item['Value'] for item in groups[0].get('Tags',[])}.get('ebkit:application')!=app:raise click.ClickException('Existing database security group is not owned by this application.')
        sg=groups[0]['GroupId']
    record.update(database_id=identifier,database_security_group=sg,database_subnet_group=identifier);save_deployment(record)
    try:ec2.authorize_security_group_ingress(GroupId=sg,IpPermissions=[{'IpProtocol':'tcp','FromPort':5432,'ToPort':5432,'UserIdGroupPairs':[{'GroupId':source}]}])
    except ClientError as error:
        if error.response.get('Error',{}).get('Code')!='InvalidPermission.Duplicate':raise
    try:rds.create_db_subnet_group(DBSubnetGroupName=identifier,DBSubnetGroupDescription='Private EBKit database subnets',SubnetIds=subnets,Tags=tags)
    except ClientError as error:
        if error.response.get('Error',{}).get('Code')!='DBSubnetGroupAlreadyExists':raise
        rows=rds.describe_db_subnet_groups(DBSubnetGroupName=identifier).get('DBSubnetGroups',[])
        if len(rows)!=1 or rows[0].get('VpcId')!=vpc or {item['Key']:item['Value'] for item in rds.list_tags_for_resource(ResourceName=rows[0]['DBSubnetGroupArn']).get('TagList',[])}.get('ebkit:application')!=app:raise click.ClickException('Existing database subnet group is not owned by this application.')
    rds.create_db_instance(DBInstanceIdentifier=identifier,Engine='postgres',DBInstanceClass=instance,AllocatedStorage=size,StorageType='gp3',MasterUsername='ebkitadmin',ManageMasterUserPassword=True,DBName='ebkit',VpcSecurityGroupIds=[sg],DBSubnetGroupName=identifier,PubliclyAccessible=False,StorageEncrypted=True,BackupRetentionPeriod=7,DeletionProtection=True,MultiAZ=data.get('multiAz',False),AutoMinorVersionUpgrade=True,Tags=tags)
    return {'database':identifier,'status':'creating','retention':'External RDS database persists after EB environment deletion. Credentials are managed by Secrets Manager.'}


def database_action(action,session,eb,app,env,record,data):
    if action=='database-create':return database_create(session,eb,app,env,record,data)
    db=owned_database(session,app);identifier=db['DBInstanceIdentifier'];endpoint=db.get('Endpoint',{})
    if action=='database-status':return {'database':identifier,'status':db['DBInstanceStatus'],'endpoint':endpoint,'public':db.get('PubliclyAccessible'),'encrypted':db.get('StorageEncrypted'),'deletionProtection':db.get('DeletionProtection'),'secretArn':db.get('MasterUserSecret',{}).get('SecretArn')}
    if action=='database-network':
        source=data.get('applicationSecurityGroup','');groups=db.get('VpcSecurityGroups',[])
        if not re.fullmatch(r'sg-[a-f0-9]+',source) or not groups:raise click.UsageError('Provide applicationSecurityGroup.')
        ec2=session.client('ec2');rows=ec2.describe_security_groups(GroupIds=[source,groups[0]['VpcSecurityGroupId']]).get('SecurityGroups',[])
        if len(rows)!=2 or rows[0]['VpcId']!=rows[1]['VpcId']:raise click.UsageError('Security groups must belong to the same VPC.')
        ec2.authorize_security_group_ingress(GroupId=groups[0]['VpcSecurityGroupId'],IpPermissions=[{'IpProtocol':'tcp','FromPort':5432,'ToPort':5432,'UserIdGroupPairs':[{'GroupId':source}]}]);return {'database':identifier,'network':'Only the supplied security group was granted PostgreSQL access.'}
    if action=='database-snapshot':
        name=identifier+'-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S');session.client('rds').create_db_snapshot(DBSnapshotIdentifier=name,DBInstanceIdentifier=identifier);return {'snapshot':name,'status':'creating'}
    if action=='database-delete':
        if data.get('databaseConfirmation')!=identifier:raise click.UsageError('Confirm the exact database identifier.')
        snapshot=identifier+'-final-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
        session.client('rds').modify_db_instance(DBInstanceIdentifier=identifier,DeletionProtection=False,ApplyImmediately=True)
        session.client('rds').delete_db_instance(DBInstanceIdentifier=identifier,SkipFinalSnapshot=False,FinalDBSnapshotIdentifier=snapshot,DeleteAutomatedBackups=False)
        return {'database':identifier,'status':'deleting','finalSnapshot':snapshot,'retention':'Snapshot and automated backups are retained; network resources are retained for explicit cleanup.'}
    if db.get('DBInstanceStatus')!='available' or not endpoint:raise click.ClickException('Wait for the database to become available.')
    arn=db.get('MasterUserSecret',{}).get('SecretArn')
    if not arn:raise click.ClickException('Managed database credentials are unavailable.')
    credentials=json.loads(session.client('secretsmanager').get_secret_value(SecretId=arn)['SecretString'])
    if action=='database-attach':
        variable=data.get('variable','DATABASE_URL')
        if variable not in {'DATABASE_URL','POSTGRES_URL','POSTGRESQL_URL'}:raise click.UsageError('Choose DATABASE_URL, POSTGRES_URL or POSTGRESQL_URL.')
        current=manage.settings(eb,app,env);raw=next((item.get('Value','{}') for item in current if item.get('Namespace')==manage.ENV_NAMESPACE and item.get('OptionName')=='env-variables'),'{}');variables=json.loads(raw)
        url='postgresql://'+quote(credentials['username'],safe='')+':'+quote(credentials['password'],safe='')+'@'+endpoint['Address']+':'+str(endpoint['Port'])+'/ebkit?sslmode=require'
        variables[variable]=url
        eb.update_environment(EnvironmentName=env,OptionSettings=[{'Namespace':manage.ENV_NAMESPACE,'OptionName':'env-variables','Value':json.dumps(variables)}])
        return {'database':identifier,'attached':variable+' merged in AWS configuration; no credentials were printed. Update this setting after rotating the managed password.'}
    try:import psycopg
    except ImportError:raise click.ClickException('Install ebkit[database] to enable PostgreSQL connection checks and SQL migrations.') from None
    try:
        tunnel_port = data.get('tunnelPort')
        if tunnel_port is not None and (type(tunnel_port) is not int or not 1 <= tunnel_port <= 65535):
            raise click.UsageError('tunnelPort must be an integer between 1 and 65535.')
        with psycopg.connect(host='127.0.0.1' if tunnel_port is not None else endpoint['Address'],port=tunnel_port if tunnel_port is not None else endpoint['Port'],dbname='ebkit',user=credentials['username'],password=credentials['password'],sslmode='require',connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                if action=='database-check':return {'database':identifier,'connection':'passed','query':'SELECT 1','network':'The CLI/worker must have a route into the private VPC.'}
                migrations=data.get('migrations')
                if not isinstance(migrations,list) or not migrations or len(migrations)>50:raise click.UsageError('Provide reviewed migrations as {id,sql} objects.')
                cursor.execute('SELECT pg_advisory_xact_lock(%s)',(int(hashlib.sha256(app.encode()).hexdigest()[:15],16),))
                cursor.execute('CREATE TABLE IF NOT EXISTS ebkit_schema_migrations (id TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())')
                applied=[]
                for migration in migrations:
                    if not isinstance(migration,dict) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',migration.get('id','')) or not isinstance(migration.get('sql'),str) or not migration['sql'].strip() or len(migration['sql'])>65536:raise click.UsageError('Invalid migration definition.')
                    if migration_has_transaction_control(migration['sql']) or re.search(r'ebkit_schema_migrations',migration['sql'],re.IGNORECASE):raise click.UsageError('Migration SQL cannot control transactions or modify the migration ledger.')
                    checksum=hashlib.sha256(migration['sql'].encode()).hexdigest();cursor.execute('SELECT checksum FROM ebkit_schema_migrations WHERE id=%s',(migration['id'],));prior=cursor.fetchone()
                    if prior:
                        if prior[0]!=checksum:raise click.UsageError('Previously applied migration content changed. Add a new migration instead.')
                        continue
                    cursor.execute(migration['sql']);cursor.execute('INSERT INTO ebkit_schema_migrations (id,checksum) VALUES (%s,%s)',(migration['id'],checksum));applied.append(migration['id'])
                return {'database':identifier,'appliedMigrations':applied,'transaction':'committed only if every migration succeeded; application release rollback does not undo schema changes.'}
    except click.ClickException:raise
    except Exception:raise click.ClickException('Database connection/migration failed. Check private routing, PostgreSQL permissions and reviewed SQL; credentials were not printed.') from None


@click.command('operations')
@click.argument('action',type=click.Choice(ACTIONS))
@manage.target_options
@click.option('--file',type=click.Path(exists=True,dir_okay=False),help='Reviewed JSON configuration for this operation.')
@click.option('-y','--yes',is_flag=True)
@manage.aws_errors
def operations_command(action,app,environment_name,region,file,yes):
    """Operate databases, metrics, alarms, autoscaling and budgets."""
    data=config_file(file)
    detached=action in {'cost','budget','leftovers','database-create','database-status','database-check','database-delete','database-snapshot','database-migrate','database-network'}
    if detached and app and environment_name:
        selected=deployment_ops._region_option(region);session,account=deployment_ops._aws_session(selected);env=environment_name
        records=deployment_ops._matching_deployments(account,selected,app,env);record=records[-1] if records else {}
    else:session,app,env,record=manage.target(app,environment_name,region)
    eb=session.client('elasticbeanstalk')
    if not detached:environment(eb,app,env)
    if action not in READ_ACTIONS:manage.confirm_change(env,f'Apply {action}; additional AWS charges or data changes can result.',yes)
    if action=='pipeline':
        from ebkit.pipeline import generate_pipeline
        try:report=generate_pipeline(app,env,session.region_name,record.get('source_url','').removesuffix('.git'),data)
        except ValueError as error:raise click.ClickException(str(error)) from None
    elif action.startswith('database-'):report=database_action(action,session,eb,app,env,record,data)
    elif action=='metrics':report=monitoring(session,eb,env,data)
    elif action=='alarm':report=alarm(session,eb,app,env,data)
    elif action=='autoscale':report=autoscale(eb,app,env,data)
    elif action=='cost':report=cost_report(session,env,data)
    elif action=='budget':report=budget(session,app,env,data)
    else:report=leftovers(session,eb,app,env)
    emit(report)
