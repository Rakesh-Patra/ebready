"""Private, short-lived S3 source input for remote CodeBuild ZIP builds."""
import hashlib
from pathlib import Path
from botocore.exceptions import ClientError

def source_bucket(account: str, region: str, application: str) -> str:
    suffix = hashlib.sha256(application.encode()).hexdigest()[:16]
    return f'ebkit-source-{account}-{region}-{suffix}'

def upload_source(s3, archive: Path, account: str, region: str, application: str) -> dict:
    bucket=source_bucket(account,region,application)
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as error:
        if error.response['Error']['Code'] not in ('404','NoSuchBucket','NotFound'): raise
        arguments={'Bucket':bucket}
        if region!='us-east-1': arguments['CreateBucketConfiguration']={'LocationConstraint':region}
        s3.create_bucket(**arguments)
        s3.put_bucket_tagging(Bucket=bucket,Tagging={'TagSet':[{'Key':'ManagedBy','Value':'EBKit'},{'Key':'EBKitApplication','Value':application}]})
    else:
        tags={tag['Key']:tag['Value'] for tag in s3.get_bucket_tagging(Bucket=bucket)['TagSet']}
        if tags.get('ManagedBy')!='EBKit' or tags.get('EBKitApplication')!=application:
            raise RuntimeError('Source bucket ownership could not be verified.') from None
    s3.put_public_access_block(Bucket=bucket,PublicAccessBlockConfiguration={'BlockPublicAcls':True,'IgnorePublicAcls':True,'BlockPublicPolicy':True,'RestrictPublicBuckets':True})
    s3.put_bucket_encryption(Bucket=bucket,ServerSideEncryptionConfiguration={'Rules':[{'ApplyServerSideEncryptionByDefault':{'SSEAlgorithm':'AES256'}}]})
    s3.put_bucket_lifecycle_configuration(Bucket=bucket,LifecycleConfiguration={'Rules':[{'ID':'ExpireSourceArchives','Status':'Enabled','Filter':{'Prefix':'sources/'},'Expiration':{'Days':1},'AbortIncompleteMultipartUpload':{'DaysAfterInitiation':1}}]})
    data=archive.read_bytes()
    key=f'sources/{hashlib.sha256(data).hexdigest()}.zip'
    s3.put_object(Bucket=bucket,Key=key,Body=data,ServerSideEncryption='AES256',ContentType='application/zip')
    return {'bucket':bucket,'key':key,'location':f'{bucket}/{key}'}
