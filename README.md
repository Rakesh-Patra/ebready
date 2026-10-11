# EBKit

EBKit is a command-line tool for preparing applications and deploying a **public GitHub repository** to **AWS Elastic Beanstalk Cluster Mode**. The deployment flow is intentionally simple: give EBKit a GitHub URL and the container port; AWS CodeBuild builds the repository's root-level `Dockerfile`, pushes the image to ECR, and Elastic Beanstalk deploys it.

EBKit runs on your computer and deploys to **your AWS account**. It is not a hosted deployment service. AWS resources may incur charges.

The [EBKit web dashboard](web/README.md) adds InsForge accounts and project storage, Google sign-in, email registration/login, GitHub validation and an authenticated worker for live AWS deployment controls. Demo mode remains available for testing without AWS actions. Live mode uses the server operator's AWS account and may incur charges.

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

## First deployment: quick start

Use this path if you already have AWS access and a public GitHub repository with a
root-level `Dockerfile`. It deploys to your AWS account and may create billable
resources.

1. **Check your AWS identity and region.** Sign in with your organization's approved
   AWS profile, then verify the account before continuing:

   ```powershell
   aws sts get-caller-identity
   $env:AWS_DEFAULT_REGION = "us-east-2"
   ```

   On macOS/Linux, set the region with `export AWS_DEFAULT_REGION=us-east-2`.
   Replace the example region with the region you intend to use.

2. **Prepare the application.** If the public GitHub repository already has its
   Dockerfile at the repository root, skip to step 3. Otherwise, from the application
   folder run `ebkit init --path .`. Init may need a configured AI provider; Gemini is
   the default supported provider. Review the generated Dockerfile and files, test the
   application, then commit and push them to GitHub. You can also initialize a public
   GitHub project with `ebkit init --repo https://github.com/OWNER/REPOSITORY`.
   Init does not deploy the app or provision its database.

3. **Deploy the public GitHub repository.** Replace the URL, app name, environment,
   region, and port with your values. The first deployment asks for confirmation:

   ```powershell
   ebkit deploy https://github.com/OWNER/REPOSITORY --app my-app --environment my-app-cluster --region us-east-2 --port 8080
   ```

   If the app needs runtime settings, keep them in a local `.env` file that is
   excluded from Git and add `--env-file .env`. Do not paste secrets into shared
   terminals, source code, or a committed file. The container must listen on
   `0.0.0.0` and on the same port you provide to EBKit.

4. **Check the result and troubleshoot.**

   ```powershell
   ebkit status --app my-app --environment my-app-cluster --region us-east-2
   ebkit logs --app my-app --environment my-app-cluster --region us-east-2
   ```

   Wait for `Ready` and a healthy status before considering deployment complete.
   `ebkit diagnose --app my-app --environment my-app-cluster` provides suggestions
   if deployment fails; it does not automatically change your code or AWS resources.

If a command behaves unexpectedly, run `ebkit --help` or `ebkit COMMAND --help`.
For interactive workflows, omit `--yes`; it skips confirmation prompts and should
only be used when you intentionally want non-interactive execution.

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

EBKit uses an operator/caller role plus AWS service roles for Cluster Mode and
CodeBuild. Review the role names, managed policies, inline build policy, and caller
permissions in the [AWS IAM guide](docs/aws-iam.md). Use least privilege; do not use
`AdministratorAccess` as a routine deployment role.

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

Use a local, gitignored `.env` file or repeated `--env KEY=VALUE` options for
application runtime settings. EBKit sends them to the running app; it does not
provision databases or run migrations. Do not commit real environment files.

For Devboard-specific connection settings and deployment instructions, see
[Deploying Devboard](docs/devboard-deployment.md).

## Generate deployment files with `ebkit init`

Run `ebkit init --path .` to prepare a local project or `ebkit init --repo URL` for
a public GitHub repository. Init generates or validates deployment files, including
a root Dockerfile; it does not deploy the app or provision databases. Docker Scout
scans source dependencies by default; local build/runtime verification is optional.

See the [init guide](docs/initialization.md) for AI provider setup, validation
behavior, security scanning, and build options.

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

### IAM permissions for `destroy`

The destroy operator role needs environment describe/terminate actions. Optional
artifact cleanup also needs the applicable Elastic Beanstalk, CodeBuild, CloudWatch
Logs, and ECR actions. See the [AWS IAM guide](docs/aws-iam.md) for complete action
lists, scoping guidance, and the `AdministratorAccess` warning.

Elastic Beanstalk schedules managed EKS cluster deletion three hours after its last
environment terminates. EBKit does not manually delete the cluster or stack; use
`cleanup-status` to inspect recorded cleanup. If termination times out, artifact
cleanup does not run. `--keep-artifacts` skips artifact cleanup.

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

Run `aws sts get-caller-identity` and confirm you are using the intended account. If
access is denied, note the denied action and resource, then ask your AWS administrator
to review the [AWS IAM guide](docs/aws-iam.md). An `iam:CreateRole` denial during
provisioning cannot be fixed by retrying with unchanged permissions.

### Build or deployment failed

Check the CodeBuild logs in the AWS Console for image-build failures. Check the Elastic Beanstalk environment and recent events for deployment failures:

```powershell
aws elasticbeanstalk describe-environments --application-name my-app --environment-names my-app-cluster --region us-east-2 --query "Environments[0].{Status:Status,Health:Health,Version:VersionLabel,CNAME:CNAME}" --output table --no-cli-pager
aws elasticbeanstalk describe-events --environment-name my-app-cluster --region us-east-2 --max-records 15 --query "Events[*].[EventDate,Severity,Message]" --output table --no-cli-pager
```

Replace the application, environment, and region with yours. A deployment is ready when the environment reports `Ready` and a healthy status.

### AWS CLI does not recognize `--image-configuration`

EBKit uses the AWS CLI to register Cluster image versions when the installed Boto3 version does not support that API shape. Install or update to the current AWS CLI v2 using the [official installation guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), open a new terminal, and verify with `aws --version`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and pull request guidance.

## Current limitations

- Deployment accepts public GitHub repositories with a root-level Dockerfile only. Local directories, prebuilt image tags, and Compose deployments are not supported.
- The application image should not bundle a local database for production. Use a managed database service; application-specific migrations and rollback are not automated.
- Gemini is the only active AI provider for `ebkit init`.
