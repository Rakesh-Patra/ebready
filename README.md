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

## Command reference

- **`ebkit init`** — Generate the root Dockerfile and deployment files, validate
  them, and scan source dependencies with Docker Scout. Interactive init asks before
  a local build; `--no-scout` skips security scanning and reports it as unverified.
- **`ebkit deploy GITHUB_URL`** — Build the application's root Dockerfile in AWS
  CodeBuild, push the image to ECR, and deploy to Elastic Beanstalk Cluster Mode.
- **`ebkit status`** — Show environment status, health, URL, and deployed version.
- **`ebkit envlist`** — List environments in the selected AWS account and region.
- **`ebkit logs`** — Show build logs, deployment events, and application container
  logs. Select one with `--source build`, `deployment`, or `application`.
- **`ebkit diagnose`** — Analyze deployment failures and suggest fixes. `--gemini`
  optionally sends sanitized diagnostic context to Gemini; fixes are advisory.
- **`ebkit resources`** — Show live environment resource identifiers and locally
  recorded image/build identifiers. `--json` provides machine-readable output.
- **`ebkit config`** — List application variable names with values hidden. Use
  `--env-file`, repeated `--env KEY=VALUE`, or `--unset KEY` to update settings
  without rebuilding the image.
- **`ebkit scale --min 1 --max 3`** — Change application replica bounds. Cluster
  Mode requires at least one replica; this does not pause EKS or stop cluster charges.
- **`ebkit versions`** — List existing application versions available for rollback.
- **`ebkit rollback --version LABEL`** — Deploy an existing application version
  without rebuilding. Its image must still exist; database migrations and environment
  settings are not rolled back.
- **`ebkit destroy`** — Terminate the selected environment, wait for termination,
  and clean up eligible deployment artifacts. Shared resources and external databases
  are retained; `--keep-artifacts` skips artifact cleanup.
- **`ebkit cleanup-status`** — Check deletion of the managed cluster stack recorded
  by destroy. Pending cluster cleanup can still incur charges.

Use `--app`, `--environment`, and `--region` on management commands to select a
deployment explicitly, or use the saved deployment/configuration. Inspect options with:

```powershell
ebkit --help
ebkit deploy --help
ebkit config --help
ebkit destroy --help
```

**There is no Cluster Mode pause command or scale-to-zero option.** AWS's
[pause/resume procedure](https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/environment-management-pause.html)
sets an EC2 Auto Scaling group's capacity to zero for load-balanced Standard environments.
EBKit uses EKS Cluster Mode, whose [replica bounds start at one](https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/configuring-cluster-scaling.html).
Use `scale --min 1 --max 1` to reduce application capacity, or `destroy` when the
deployment is no longer needed. EKS deletion is service-managed and scheduled three
hours after its last environment terminates; charges continue until deletion completes.

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

The initial IAM setup can be involved. If the list feels tedious and you are tempted to grant `AdministratorAccess` just to get past setup, do not use that as a shortcut: it grants broad access to your AWS account beyond what EBKit needs. Ask your AWS administrator to create or provide a dedicated deployment role with the permissions above instead.

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

The `.env` file stays on your computer; **you do not push it to GitHub**. Deploy reads
the local file and configures its values as application environment variables in AWS.
CodeBuild gets your code and root Dockerfile from GitHub. These application settings
are supplied to the deployed container, not to the Docker build.

For Devboard, create or edit `devboard/.env` with your database provider's actual values:

```dotenv
POSTGRES_URL=postgresql://USER:PASSWORD@HOST:5432/DATABASE?sslmode=require
PORT=8080
```

Use a database endpoint reachable from AWS and the TLS settings required by your
provider. Replace every placeholder. Other applications may use different variable
names; init lists detected settings, and `.env.example` documents the expected keys.
Commit `.env.example` with blank values, and keep `.env` gitignored.

Run deploy from the folder containing `.env`, or specify its path. For example,
from the parent folder containing the `devboard` checkout:

```powershell
ebkit deploy https://github.com/Rakesh-Patra/devboard.git --app devboard --environment devboard-cluster --port 8080 --env-file ./devboard/.env
```

An absolute file path also works. Alternatively, pass settings directly:

```powershell
ebkit deploy https://github.com/Rakesh-Patra/devboard.git --app devboard --environment devboard-cluster --port 8080 --env "POSTGRES_URL=YOUR_CONNECTION_URL"
```

Repeat `--env KEY=VALUE` for additional settings. You can combine `--env-file` and
`--env`; command-line values override matching keys in the file.

## Generate deployment files with `ebkit init`

By default, `ebkit init` generates a root Dockerfile and deployment files, validates
them statically, and runs Docker Scout against source dependencies using `fs://`.
It does **not** build an application image or start a local container. This source
scan does not verify the packages in a built image. Use `--no-scout` to skip Scout;
if Scout is unavailable, init reports security validation as unverified.

If the source security gate fails, deployment files remain generated, but readiness
is blocked. Init lists affected packages, manifest paths, and Scout's reported fixed
versions. Update dependencies and lockfiles, test compatibility, and rerun init;
regenerating the Dockerfile does not fix source dependency CVEs. Some findings have
no available fix. `--no-scout` remains an explicit option to skip scanning; it does
not fix vulnerabilities.

Local image verification is optional: request it with `ebkit init --build`.
Interactive init also asks `Build the Docker image locally? [y/N]` after artifact validation.
Press Enter to skip. `--no-build` skips this prompt, and `--yes` skips building unless
you explicitly pass `--build` or `--runtime`.
`--runtime` also explicitly requests a local build and runtime verification.
`--scout` alone scans source dependencies and never triggers a build.

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

The wizard scans a local project or a public GitHub repository and generates or validates the deployment files, including one root-level `Dockerfile`. For a GitHub source, use:

```powershell
ebkit init --repo https://github.com/owner/project
```

EBKit clones into a folder named `project` in the current directory and retains that clone after init completes. If that folder already exists, init stops without overwriting it. Multi-tier projects are scanned and EBKit asks Docker AI (Gordon) to generate one root-level Dockerfile; if Gordon is unavailable or returns an invalid result, Gemini is used as a fallback. Gemini receives scan metadata and manifest paths, not source or manifest contents; `.env` values are not sent. Generation is best-effort: inspect the Dockerfile, verify it builds and serves every required tier, and run the generated app locally before deploying. EBKit's static validation cannot prove that arbitrary services work together at runtime.

Compose databases, Redis, and other detected stateful dependencies are not bundled into the application container, and `ebkit init` does not provision managed services. Init reports these services, adds detected connection-variable names (never values) to `.env.example`, and creates or safely updates a local `.env` with blank missing settings. It also ensures `.env` is ignored by Git and prints a ready-to-edit `ebkit deploy ... --env-file .env` command. Existing `.env` values are preserved and never printed. Provision the required managed database/cache/broker separately, configure its network access, and fill in the connection values locally before deploying. Alternatively, pass individual values with repeated `--env KEY=VALUE` options. Never commit the real `.env` file. Then commit and push the application and root Dockerfile to GitHub before running the printed deploy command.

## Inspect and manage deployments

EBKit uses the current AWS CLI/Boto3 credentials and saved AWS region. `--app`, `--environment`, and `--region` can be supplied to select an environment; otherwise, EBKit uses its saved configuration or most recent deployment metadata.

```powershell
ebkit status --app my-app --environment my-app-cluster
ebkit envlist --region us-east-2
ebkit logs --app my-app --environment my-app-cluster --source all --lines 100
ebkit diagnose --app my-app --environment my-app-cluster
```

`status` and `envlist` query Elastic Beanstalk for current environment status, health, URL, and version. `logs` retrieves CodeBuild logs and Elastic Beanstalk events. `diagnose` provides deterministic rule-based recommendations; pass `--gemini` to send sanitized event/build-log context to Gemini for additional advice. Gemini retries are bounded (at most three retries). Diagnosis is advisory and does not edit source code or AWS resources.

Deployment metadata is stored locally at `~/.ebkit/deployments.json` (or the path in `EBKIT_STATE_FILE`). It contains deployment identifiers and public source metadata, not environment-variable values or AWS credentials.

To terminate a deployment environment:

```powershell
ebkit destroy --app my-app --environment my-app-cluster
```

`destroy` requires typing the exact environment name. It requests termination and waits
up to 20 minutes (`--timeout SECONDS` changes this) before cleaning up eligible artifacts.
It deletes the environment's CloudWatch log/metric streams, keeping the shared Cluster
log groups and other environments' streams. If no active environment remains in the
application, it removes matching EBKit-tagged CodeBuild projects, stops their running
builds, deletes their build log groups, and removes the EBKit-tagged ECR repository and
its images. Empty applications created by EBKit and their versions are also removed.
Logs and images are permanently deleted. IAM roles and external databases are retained.
Existing resources without matching ownership tags are reported as retained; those
resources can still incur charges. New repositories and build projects created by
deploy receive ownership tags. Cleanup requires the corresponding AWS delete permissions.

Elastic Beanstalk owns the EKS cluster and its compute infrastructure. It schedules
cluster deletion **three hours after the last environment on that cluster is terminated**.
Other environments or a new deployment can keep that cluster active, and charges
continue until deletion completes. EBKit records the cluster ARN and prints the
read-only CloudFormation verification command. It does not manually delete the
service-managed cluster or stack. See [AWS cluster deletion documentation](https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/beanstalk-cluster-concepts.html#beanstalk-cluster-concepts-deletion).

If termination times out or cleanup fails, destroy exits with an error and reports
pending cleanup rather than claiming that all charges have stopped. You can retry
cleanup for a recently terminated environment. Use `--keep-artifacts` to request only
termination and preserve images, build projects and logs.

## Manage the application from your terminal

These commands use the same `--app`, `--environment`, and `--region` target selection
as status. Without an explicit target, EBKit uses its saved deployment/configuration.
Commands that change cloud settings ask for confirmation; `--yes` skips that prompt.
Updates are asynchronous: use `status` and `logs` to check completion.

```powershell
ebkit resources --app my-app --environment my-app-cluster
ebkit logs --source application --lines 100
ebkit config
ebkit config --env-file .env --env "LOG_LEVEL=info"
ebkit config --unset OLD_SETTING
ebkit scale --min 1 --max 3
ebkit versions
ebkit rollback --version EXISTING_VERSION_LABEL
ebkit cleanup-status --app my-app --environment my-app-cluster
```

`resources` displays live Beanstalk resource identifiers plus locally recorded ECR
image/CodeBuild identifiers; `--json` produces machine-readable metadata. It is not
a cost estimate. `logs --source application` reads the selected environment's container
logs from the default shared CloudWatch group for the last hour; `all` includes these
alongside build logs and deployment events. Other logging backends are not queried.

`config` lists variable names with all values hidden. With `--env-file` or repeated
`--env`, it merges supplied values with existing settings, without rebuilding the image.
Flags override matching file values. Blank settings are rejected; use `--unset` to
remove a key. An unchanged `PORT` in the file is accepted, but changing/removing it
requires redeploying with `--port`. Values are never printed or saved in local history.

`scale` changes replica bounds from 1 to 100; minimum must not exceed maximum. It
does not pause the cluster or stop EKS charges. A redeploy preserves existing settings
and replica bounds, overrides explicitly supplied environment values, and updates PORT.

`versions` lists existing application versions, with pagination. `rollback` requests
deployment of an explicit existing label without rebuilding; its image must still exist
in ECR. Configuration changes and database migrations are not rolled back.

`cleanup-status` uses the locally recorded cluster ARN to check its CloudFormation
stack, including after the application has been deleted. It reports pending deletion
rather than treating environment termination as proof that cluster charges stopped.
Its artifact cleanup summary is historical, not a fresh inventory of every AWS resource.

See [AWS Cluster configuration options](https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/command-options-general-eks.html)
for replica bounds and application variables. These commands require the corresponding
AWS read/update permissions. EBKit manages Beanstalk deployments, not unrelated resources
throughout your AWS account.

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
