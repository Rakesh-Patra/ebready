# AWS IAM roles and permissions

EBKit runs locally with the caller identity provided by AWS CLI/Boto3 credentials.
Use a dedicated operator/deployment IAM role. The caller role is separate from the
service roles that AWS assumes. Scope permissions to the account, region, resources,
and role ARNs wherever AWS supports it.

## Roles created or used by deploy

| Role | Trusted service | Policies attached by EBKit |
|---|---|---|
| `aws-elasticbeanstalk-eks-cluster-role` | `eks.amazonaws.com` | `AmazonEKSClusterPolicy`; `AWSElasticBeanstalkCustomPlatformforEC2Role` |
| `aws-elasticbeanstalk-eks-node-role` | `ec2.amazonaws.com` | `AmazonEKSWorkerNodePolicy`; `AmazonEC2ContainerRegistryReadOnly`; `AmazonEKS_CNI_Policy`; `AWSElasticBeanstalkMulticontainerDocker` |
| `aws-elasticbeanstalk-eks-observability-role` | `ec2.amazonaws.com` | `CloudWatchAgentServerPolicy` |
| Repository-specific `ebkit-…-build-role` | `codebuild.amazonaws.com` | Inline policy `EBKitBuildAndPushImage` |
| `AWSServiceRoleForElasticBeanstalk` | `elasticbeanstalk.amazonaws.com` | AWS-managed service-linked role, created if absent |

The AWS-managed policy ARNs attached to the three Cluster Mode roles are:

- `arn:aws:iam::aws:policy/AmazonEKSClusterPolicy`
- `arn:aws:iam::aws:policy/AWSElasticBeanstalkCustomPlatformforEC2Role`
- `arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy`
- `arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly`
- `arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy`
- `arn:aws:iam::aws:policy/AWSElasticBeanstalkMulticontainerDocker`
- `arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy`

The CodeBuild role's `EBKitBuildAndPushImage` policy grants `ecr:GetAuthorizationToken`
on `"*"`; ECR layer upload and `ecr:PutImage` actions on the app repository; and
`logs:CreateLogGroup`, `logs:CreateLogStream`, and `logs:PutLogEvents` on that build
project's CloudWatch log group.

## Caller role for deploy

New IAM roles and policy changes can take time to propagate. EBKit retries
CodeBuild project creation/update and build start only when AWS reports
`InvalidInputException` mentioning `sts:AssumeRole`, with six bounded waits
(2, 4, 8, 16, 30 and 30 seconds). Other permission and input errors fail
immediately. If role assumption still fails, check the exact service-role ARN,
its trust for `codebuild.amazonaws.com`, and STS availability in the target
region. Administrator permissions on the caller do not replace the service
role's trust policy.

Creating ECR repositories with EBKit's ownership tags requires both
`ecr:CreateRepository` and `ecr:TagResource` on the repository ARN. Giving only
`CreateRepository` causes creation to fail with `AccessDeniedException` for
`TagResource`. These permissions belong to the deployment caller, not the
CodeBuild service role. See [AWS ECR tag authorization](https://docs.aws.amazon.com/AmazonECR/latest/userguide/ecr-supported-iam-actions-tagging.html).

For this workspace's website deployments, the narrow supplemental policy in
[ebkit-ecr-tag-permission.json](ebkit-ecr-tag-permission.json) allows tagging
`ebk-*` repositories in account `717056864326`, region `us-east-2`. An IAM
administrator must attach it to `ebready-agent`; the file does not change AWS
permissions by itself. Adapt the account, region and repository prefix for
other deployments. This supplemental policy resolves the tagging denial only;
the caller still needs the other deployment permissions below.

The deploy caller needs permission to create/read and use the service roles, attach
the listed AWS-managed policies, pass roles to AWS, create the Elastic Beanstalk
service-linked role if it does not exist, and manage the required CodeBuild project,
ECR repository, application/version/environment, and read default VPC/subnet details.
The IAM setup includes:

- `iam:GetRole`, `iam:CreateRole`, `iam:AttachRolePolicy`, and `iam:PutRolePolicy`
- `iam:PassRole` for the CodeBuild, Cluster, node, observability, and service roles
  when applicable
- `iam:CreateServiceLinkedRole` if the Elastic Beanstalk service-linked role is absent

AWS services assume their own service roles using their trust policies. Do not attach
those service-role policies to the caller role as a substitute for `iam:PassRole`.
An `iam:CreateRole` denial during first-time provisioning cannot be fixed by retrying
with unchanged permissions.

## Caller role for destroy

Termination requires:

- `elasticbeanstalk:DescribeEnvironments`
- `elasticbeanstalk:DescribeEnvironmentResources`
- `elasticbeanstalk:TerminateEnvironment`

Without `--keep-artifacts`, eligible cleanup additionally requires:

- Elastic Beanstalk: `elasticbeanstalk:DescribeApplications`,
  `elasticbeanstalk:DeleteApplication`
- CodeBuild: `codebuild:BatchGetProjects`, `codebuild:ListBuildsForProject`,
  `codebuild:BatchGetBuilds`, `codebuild:StopBuild`, `codebuild:DeleteProject`
- CloudWatch Logs: `logs:DescribeLogStreams`, `logs:DeleteLogStream`,
  `logs:DeleteLogGroup`
- ECR: `ecr:DescribeRepositories`, `ecr:ListTagsForResource`,
  `ecr:DeleteRepository`

EBKit calls `sts:GetCallerIdentity` to find the account; AWS does not require an
explicit allow for that action. EBKit does not run a permission preflight or block
commands based on role name. AWS authorizes each API call. Some listing/discovery
actions require `"Resource": "*"`. Do not grant permission to manually delete the
service-managed EKS cluster or CloudFormation stack: Elastic Beanstalk manages that
cleanup, which may take three hours after its last environment terminates.

## Least privilege

Ask an AWS administrator to review and approve a scoped role. `AdministratorAccess`
grants broad account control and should not be a routine or permanent role. Only use
it as an explicitly approved, temporary break-glass measure.
