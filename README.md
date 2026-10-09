# EBKit

EBKit is a command-line tool for preparing applications and deploying a **public GitHub repository** to **AWS Elastic Beanstalk Cluster Mode**. The deployment flow is intentionally simple: give EBKit a GitHub URL and the container port; AWS CodeBuild builds the repository's root-level `Dockerfile`, pushes the image to ECR, and Elastic Beanstalk deploys it.

EBKit runs on your computer and deploys to **your AWS account**. It is not a hosted deployment service. AWS resources may incur charges.

## Before you start

You will need:

- **Python 3.11 or newer**
- **AWS CLI v2**, installed and configured for your AWS account
- An AWS account with permission to use Elastic Beanstalk Cluster Mode, CodeBuild, ECR, IAM, EC2/VPC, and related services
- A **public GitHub repository** with a `Dockerfile` at its root
- The application listening on `0.0.0.0` and the port you will pass to EBKit
- A **Gemini API key** only if you plan to run `ebkit init`

EBKit does not need Docker installed locally to deploy. CodeBuild builds the image in AWS. Cluster Mode provisions AWS infrastructure and may cost money while it is running; review AWS pricing and clean up environments you no longer need. Never share AWS credentials or API keys.

## Install EBKit

EBKit is currently installed from this GitHub repository:

```powershell
git clone https://github.com/Rakesh-Patra/ebready.git
cd ebready
py -m pip install .
ebkit --help
```

On macOS or Linux, use `python3 -m pip install .` instead of `py -m pip install .`.
To install the latest development version later, go to the cloned `ebready` folder and run the install command again. A PyPI package is not currently published.

## Set up AWS access

Install AWS CLI v2 using the [official AWS installation guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), then configure credentials. For local development, AWS IAM Identity Center (SSO) is preferred when your organization provides it.

For an SSO profile:

```powershell
aws configure sso
aws sso login
$env:AWS_PROFILE = "your-profile-name"
aws sts get-caller-identity
```

For a locally configured credentials profile:

```powershell
aws configure
aws sts get-caller-identity
```

Confirm the last command shows the AWS account and identity you intend to use. EBKit and its AWS CLI fallback use the same AWS profile. Do not paste credentials into source files, command examples, or chat.

## Deploy from GitHub

Use a public repository URL. EBKit does not accept local paths or local Docker images:

```powershell
ebkit deploy https://github.com/owner/project --app my-app --environment my-app-cluster --region us-east-2 --port 5000 -y
```

Set `--port` to the port the application listens on inside the container. It defaults to `8080`. This must match the application's actual listening port and the Dockerfile's `EXPOSE` port when present. The URL's default branch is built.

EBKit performs the deployment without a local Docker build:

1. CodeBuild pulls the public repository and builds its root `Dockerfile`.
2. CodeBuild pushes the image to an ECR repository named after the application.
3. EBKit registers the image as an Elastic Beanstalk Cluster application version.
4. EBKit creates the Cluster environment or updates the existing one.
5. EBKit waits for the environment to become ready and prints its URL.

The first deployment may create IAM roles, a CodeBuild project, an ECR repository, and Elastic Beanstalk resources. These are created in your AWS account using your credentials. You do not need to create a CodeBuild project, ECR repository, or EKS cluster manually. Private GitHub repositories are not supported by this flow.

The source bundle must have the Dockerfile at its root. Do not put an extra parent folder around the files inside a ZIP; EBKit builds directly from the GitHub repository root.

### AWS IAM permissions and roles

The AWS identity used to run `ebkit deploy` must be allowed to provision the deployment resources. Having permission to deploy one application does not necessarily grant permission to create the roles or resources for another. In particular, EBKit creates a repository-specific CodeBuild service role; AWS may reject a first deployment with `AccessDenied` for `iam:CreateRole` before the image build starts.

Ask your AWS administrator to authorize the deploy identity for the following operations, scoped to the account, region, resource names, and role ARNs where supported:

- **IAM role setup:** `iam:GetRole`, `iam:CreateRole`, `iam:AttachRolePolicy`, and `iam:PutRolePolicy`; `iam:PassRole` for the repository-specific CodeBuild role and the Cluster, node, and observability roles; and `iam:CreateServiceLinkedRole` for `elasticbeanstalk.amazonaws.com` when that service-linked role is not already present.
- **Cluster service roles:** permission to attach the AWS-managed policies used by Cluster Mode. EBKit checks or creates `aws-elasticbeanstalk-eks-cluster-role`, `aws-elasticbeanstalk-eks-node-role`, and `aws-elasticbeanstalk-eks-observability-role`. These roles trust EKS or EC2 as appropriate and use the EKS cluster, worker-node, ECR read-only, CNI, Elastic Beanstalk platform, and CloudWatch Agent policies.
- **Build role:** permission to create or update the repository-specific CodeBuild project and start/read its builds. EBKit adds an inline policy to its CodeBuild role for ECR image upload and CloudWatch Logs writes.
- **Deployment resources:** permissions for the required ECR repository operations, Elastic Beanstalk application/version/environment create or update operations, and EC2 default VPC/subnet reads.

The IAM identity that runs EBKit is separate from the service roles used by CodeBuild and Elastic Beanstalk. The deploy identity needs permission to pass the service roles to AWS; AWS services then assume those roles using their trust policies. Do not solve an access-denied error by sharing credentials or granting unrestricted administrator access. Have an administrator review and approve a least-privilege policy for your account. If the roles already exist, the deploy identity still needs permission to read and use them, and to pass them where required.

An earlier successful deployment may have used roles that were already present or an AWS identity with broader permissions. A later deployment can still fail if it needs a new role, such as the CodeBuild role for a different repository.

### Options
| Option | What it does |
|---|---|
| `SOURCE` | Required public GitHub repository URL |
| `--app` | Elastic Beanstalk application name; defaults to repository name |
| `--environment`, `--env-name` | Elastic Beanstalk environment name; defaults to `<app>-cluster` |
| `--region` | AWS region; defaults to saved AWS configuration or `us-east-2` |
| `--port` | Application container port; defaults to `8080` |
| `--env-file` | Local file containing application environment variables |
| `--env KEY=VALUE` | Set an application environment variable; repeat as needed |
| `--wait / --no-wait` | Wait for environment status and URL; waits by default |
| `-y, --yes` | Skip the deployment confirmation |

To see the full command help:

```powershell
ebkit deploy --help
```

### Environment variables and databases

Provide application settings with a local, gitignored environment file or repeated `--env` options:

```powershell
ebkit deploy https://github.com/owner/project --app my-app --environment my-app-cluster --region us-east-2 --port 5000 --env-file .env.production -y
```

EBKit sends these values to the Elastic Beanstalk environment; it does not create a database, initialize schemas, or run application-specific migrations. Use a managed database such as Amazon RDS for durable production data, configure its network access securely, and run the migrations documented by your application. Do not commit real environment files or put secrets in Docker build arguments. Review who can view environment settings in AWS.

## Generate deployment files with `ebkit init`

Use the initializer if your project needs a Dockerfile or other deployment files. Get a Gemini API key from Google AI Studio and set it in your terminal before running the wizard.

**Windows PowerShell:**

```powershell
$env:GEMINI_API_KEY = "your-key"
ebkit init
```

**macOS/Linux:**

```bash
export GEMINI_API_KEY="your-key"
ebkit init
```

The wizard scans the project and can generate files such as `Dockerfile`, `.dockerignore`, `Procfile`, `.ebignore`, and `.env.example`. Review the generated files, commit and push the application (including its root Dockerfile) to a public GitHub repository, then deploy that URL with `ebkit deploy`.

## Troubleshooting

### AWS credentials or permissions

Run `aws sts get-caller-identity` and confirm you are using the intended account. If access is denied, note the denied action and resource, then ask your AWS administrator to review the [IAM permissions and roles](#aws-iam-permissions-and-roles) needed for Elastic Beanstalk Cluster Mode, CodeBuild, ECR, and the target network. An `iam:CreateRole` denial occurs during provisioning; retrying unchanged credentials will not resolve it.

### Build or deployment failed

Check the CodeBuild logs in the AWS Console for image-build failures. Check the Elastic Beanstalk environment and recent events for deployment failures:

```powershell
aws elasticbeanstalk describe-environments --application-name my-app --environment-names my-app-cluster --region us-east-2 --query "Environments[0].{Status:Status,Health:Health,Version:VersionLabel,CNAME:CNAME}" --output table --no-cli-pager
aws elasticbeanstalk describe-events --environment-name my-app-cluster --region us-east-2 --max-records 15 --query "Events[*].[EventDate,Severity,Message]" --output table --no-cli-pager
```

Replace the application, environment, and region with yours. A deployment is ready when the environment reports `Ready` and a healthy status.

### AWS CLI does not recognize `--image-configuration`

EBKit uses the AWS CLI to register Cluster image versions when the installed Boto3 version does not support that API shape. Install or update to the current AWS CLI v2 using the [official installation guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), open a new terminal, and verify with `aws --version`.

## For contributors

Clone the repository, install development dependencies, and run the tests:

```powershell
git clone https://github.com/Rakesh-Patra/ebready.git
cd ebready
py -m pip install -e ".[dev]"
py -m pytest
```

On macOS/Linux, use `python3` in place of `py`.

## Current limitations

- Deployment accepts public GitHub repositories with a root-level Dockerfile only. Local directories, prebuilt image tags, and Compose deployments are not supported.
- The application image should not bundle a local database for production. Use a managed database service; application-specific migrations and rollback are not automated.
- Gemini is the only active AI provider for `ebkit init`.
