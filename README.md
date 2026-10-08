# EBKit

EBKit is a command-line tool that helps you prepare a Dockerized application and deploy it to **AWS Elastic Beanstalk Cluster Mode (Amazon EKS)**. It builds and pushes your image to Amazon ECR, creates or updates an Elastic Beanstalk environment, and reports the deployed URL.

EBKit runs on your computer and deploys to **your AWS account**. It is not a hosted deployment service. AWS resources may incur charges.

## Before you start

You will need:

- **Python 3.11 or newer**
- **Git**
- **Docker Desktop** (Windows/macOS) or Docker Engine (Linux), installed and running
- **AWS CLI v2**, installed and configured for your AWS account
- An AWS account with permission to use Elastic Beanstalk Cluster Mode, ECR, IAM, EC2/VPC, and related services
- A **Gemini API key** only if you plan to run `ebkit init`

Cluster Mode provisions AWS infrastructure and may cost money while it is running. Review AWS pricing and clean up resources you no longer need. Never share AWS credentials or API keys.

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

Install AWS CLI v2 using the [official AWS installation guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), then configure credentials. For local development, AWS IAM Identity Center (SSO) is preferred when your organization provides it; otherwise, follow your organization’s secure credential setup.

For an SSO profile:

```powershell
aws configure sso
aws sso login
aws sts get-caller-identity
```

For a locally configured credentials profile:

```powershell
aws configure
aws sts get-caller-identity
```

The last command should show the AWS account and identity you intend to use. EBKit uses the AWS credentials available to Boto3 and the AWS CLI. Make sure both are using the same profile/account. Do not paste credentials into source files, command examples, or chat.

## Option A: Deploy a project that already has a Dockerfile

Open a terminal in your application folder and deploy:

```powershell
ebkit deploy . --app my-app --env my-app-cluster --region us-east-2 -y
```

Replace `my-app` with your application name and choose an AWS region where Elastic Beanstalk Cluster Mode is available. EBKit will:

1. Build the Docker image from the project’s `Dockerfile`.
2. Create or use an ECR repository and push the image.
3. Register that image as an Elastic Beanstalk Cluster application version.
4. Create the environment if needed, or deploy the new version to the existing environment.
5. Wait for the environment to become ready and print its URL.

To deploy a public GitHub repository that already includes a working `Dockerfile`:

```powershell
ebkit deploy https://github.com/owner/project --app my-app --env my-app-cluster --region us-east-2 -y
```

You can also run `ebkit deploy` without `-y` to use the interactive prompts.

### Docker image and port

By default, EBKit detects the first `EXPOSE` port in your Dockerfile and uses `8080` if none is found. You can set it explicitly:

```powershell
ebkit deploy . --app my-app --env my-app-cluster --region us-east-2 --port 8000 -y
```

Your application must listen on `0.0.0.0` and on the same port you configure.

### Environment variables

EBKit reads a `.env` file in your project folder if present. You can provide another file with `--env-file`:

```powershell
ebkit deploy . --app my-app --env my-app-cluster --region us-east-2 --env-file .env.production -y
```

Environment variable values are sent to AWS as part of the environment configuration. Do not commit real `.env` files to source control. For production secrets, use an appropriate AWS secrets-management approach and carefully review who can view environment settings.

### Use an image that is already in ECR

If the image is already pushed to ECR, skip building and pushing it:

```powershell
ebkit deploy . --app my-app --env my-app-cluster --region us-east-2 --tag 123456789012.dkr.ecr.us-east-2.amazonaws.com/my-app:latest --no-build --no-push -y
```

Replace the example account ID, region, repository, and tag with your own. The environment’s node role must be allowed to pull the image.

## Option B: Generate deployment files with `ebkit init`

Use this when your project needs a Dockerfile or other deployment files generated. Get a Gemini API key from Google AI Studio and set it in your terminal before running the wizard.

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

The wizard scans the project and can generate files such as `Dockerfile`, `.dockerignore`, `Procfile`, `.ebignore`, and `.env.example`. Review generated files before using them. Existing files are protected from being silently overwritten; follow the prompts if a file already exists.

After initialization, deploy from your project directory using the command in [Option A](#option-a-deploy-a-project-that-already-has-a-dockerfile).

## Useful commands

```powershell
ebkit --help
ebkit init --help
ebkit deploy --help
```

Deploy option summary:

| Option | What it does |
|---|---|
| `[SOURCE]` | Local project directory or GitHub URL; defaults to the current directory |
| `--app` | Elastic Beanstalk application name |
| `--env` | Elastic Beanstalk environment name |
| `--region` | AWS region |
| `--tag` | Docker image tag or full image URI |
| `--repo` | ECR repository name; defaults to the application name |
| `--port` | Application port; auto-detected from Dockerfile or defaults to `8080` |
| `--env-file` | Additional environment-variable file |
| `--no-build` | Do not build a Docker image |
| `--no-push` | Do not push an image to ECR |
| `--wait / --no-wait` | Wait for environment status and URL; waits by default |
| `-y, --yes` | Skip interactive prompts |

## Troubleshooting

### AWS credentials or permissions

Run `aws sts get-caller-identity` and confirm you are using the intended account. If access is denied, ask your AWS administrator to grant the permissions required for Elastic Beanstalk Cluster Mode, ECR image push/pull, IAM role setup, and the target network resources.

### Docker is unavailable

Start Docker Desktop or Docker Engine, then verify it is responding:

```powershell
docker info
```

### The environment is still deploying

Check the environment and its recent events:

```powershell
aws elasticbeanstalk describe-environments --application-name my-app --environment-names my-app-cluster --region us-east-2 --query "Environments[0].{Status:Status,Health:Health,Version:VersionLabel,CNAME:CNAME}" --output table --no-cli-pager
aws elasticbeanstalk describe-events --environment-name my-app-cluster --region us-east-2 --max-records 15 --query "Events[*].[EventDate,Severity,Message]" --output table --no-cli-pager
```

Replace the application, environment, and region with yours. A deployment is complete when the environment reports `Ready` and a healthy status.

### AWS CLI does not recognize `--image-configuration`

EBKit uses the AWS CLI to register Cluster image versions when the installed Boto3 version does not support that API shape. Install or update to the current AWS CLI v2 using the [official installation guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), open a new terminal, and verify with `aws --version`.

## Generated files

| File | Purpose |
|---|---|
| `Dockerfile` | Builds the application container |
| `.dockerignore` | Excludes files from Docker build context |
| `Procfile` | Defines the Elastic Beanstalk web process |
| `.ebignore` | Excludes development files from deployment bundles |
| `.env.example` | Lists environment-variable names without secret values |

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

- Gemini is the only active AI provider for `ebkit init`.
- The deploy workflow targets a single Docker image in Elastic Beanstalk Cluster Mode.
- Multi-container/Docker Compose deployment and automated rollback are not currently supported.
