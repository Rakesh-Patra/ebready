# EBKit

EBKit generates and checks Docker deployment kits and manages AWS Elastic Beanstalk deployments from the CLI and web.

- Production Dockerfile recipes, distroless runtimes where supported, build/runtime checks and image CVE gates.
- GitHub and reviewed ZIP deployments; GitHub automatic updates every five minutes.
- Same-environment redeployment, version history, rollback, cloning and blue/green URL swaps.
- Health/events, configuration controls and named AWS profiles.
- External PostgreSQL lifecycle, SQL migrations, CloudWatch history/alarms, guided autoscaling, verified releases, GitHub OIDC pipeline generation and scoped billing/budget tools.

**Start here:** [Features explained simply](docs/FEATURES.md) | [CLI guide](docs/CLI.md) | [Web guide](web/README.md) | [Database, monitoring and cost tools](docs/OPERATIONS.md) | [Initialization](docs/initialization.md)

For hosting this dashboard on Standard Elastic Beanstalk with the root Dockerfile,
Terraform, GitHub OIDC, Secrets Manager and CloudWatch, follow the
[DevOps implementation and deployment walkthrough](docs/DEVOPS-HACKATHON-GUIDE.md).
Its public demo disables login and cloud/AI operations until HTTPS is configured.
This hosting setup is separate from the customer Cluster Mode deployment engine.

Automated tests cover local behavior and mocked AWS integrations. Live AWS integration tests are still required. AWS resources incur charges; external databases and snapshots persist independently of environment teardown. Cost reports explicitly identify attribution limits.

---

# EBKit

EBKit is a command-line tool for preparing applications and deploying a **public GitHub repository or reviewed project ZIP** to **AWS Elastic Beanstalk Cluster Mode**. The deployment flow is intentionally simple: give EBKit a GitHub URL and the container port; AWS CodeBuild builds the repository's root-level `Dockerfile`, pushes the image to ECR, and Elastic Beanstalk deploys it.

EBKit runs on your computer and deploys to **your AWS account**. It is not a hosted deployment service. AWS resources may incur charges.

The [EBKit web dashboard](web/README.md) adds InsForge accounts and project storage, Google sign-in, email registration/login, GitHub validation and an authenticated worker for live AWS deployment controls. Demo mode remains available for testing without AWS actions. Live mode uses the server operator's AWS account and may incur charges.

## Before you start

You will need:

- **Python 3.11 or newer**
- **AWS CLI v2**, installed and configured for your AWS account
- An AWS account with permission to use Elastic Beanstalk Cluster Mode, CodeBuild, ECR, IAM, EC2/VPC, and related services
- A **public GitHub repository or local project ZIP** with a `Dockerfile` at its root
  (create and review one first with `ebkit init` or the website's ZIP/GitHub snapshot preparation flow)
- The application listening on `0.0.0.0` and the port you will pass to EBKit
- A **Gemini API key** for local AI init, or an approved EBKit website session for hosted ZIP init using the operator key

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

AWS connections automatically use the operating system's trusted certificates.
Website users do not need to configure certificate files; the deployment worker
handles TLS. An operator's explicit `AWS_CA_BUNDLE` or AWS profile `ca_bundle`
still takes precedence. Readiness checks retry brief connection failures without
starting another deployment. If checking remains unavailable, check the existing
environment's Status before redeploying.

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
   application, then commit and push them to GitHub. GitHub links are deployment-only;
   use a local directory or ZIP for init.
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

Generated Dockerfiles now pass a shared production quality gate in the CLI and
website. Supported apps use reviewed distroless recipes; Gordon/Gemini drafts must
pass the same checks before saving. The gate checks final-stage non-root users,
multi-stage builds, exec-form startup, matching runtimes, safe copy/install inputs
and ports. Runtime and CVE health are measured separately. See the
[production generation and verification guide](docs/initialization.md).

```powershell
ebkit doctor --path . --port 8080 --production --require-distroless --pin-bases --verify --scout
```

This reviews real registry digest pins, builds/probes the image, and enforces zero
Critical/High image CVEs with Docker Scout. A failed/unavailable scan is never a pass.

- **`ebkit doctor --path . --port 8080`** — Explain deployment blockers without
  executing code or contacting AWS/AI. `--fix` shows diffs and asks before applying
  targeted startup-binding, Dockerfile EXPOSE, and incompatible Node runtime repairs.
  Missing Dockerfiles/build inputs and dependency conflicts require manual review.
  Add `--verify` to approve a local Docker build and HTTP probe; the temporary
  container and image are cleaned up afterward. Build/runtime remain **not verified**
  unless those checks actually run. Security is separate: use init/Scout.
  `--json` emits the report; `--yes` explicitly approves repairs/local execution.
  Exit codes: 0 = checked without outstanding findings, 1 = findings/check failure,
  2 = requested Docker verification was not completed. Static checks are heuristic.

```powershell
ebkit doctor --path . --port 8080
ebkit doctor --path . --port 8080 --fix --verify
ebkit doctor https://github.com/OWNER/REPOSITORY --output ./reviewed-project --port 8080 --fix --verify
```

The website offers **Deployment doctor** after ZIP inspection and on Dockerfile
review. Review proposed diffs, then apply repairs to the uploaded copy. Preparation,
Dockerfile approval and Scout must be repeated after repairs. Web doctor performs
static analysis only; download/extract the reviewed prepared ZIP and run CLI
`doctor --verify` for local build/runtime verification.

GitHub doctor downloads the public default branch at an identified commit into an
empty `--output` directory, with the same bounded ZIP extraction as uploads.
Repairs change that retained snapshot; push reviewed files yourself to deploy from
GitHub, or use the reviewed prepared ZIP. On the website, choose **Check GitHub
with Deployment doctor**, review/apply diffs, prepare and review the Dockerfile,
then download the ZIP or **Use reviewed GitHub snapshot for deployment**.
Snapshot deployment follows the ZIP security gate and manual update workflow.

### Generate a production Dockerfile in the website

The web preparation flow accepts either a project ZIP or a public GitHub URL.
For ZIPs, start **New deployment → Upload ZIP**, upload the archive, inspect and
confirm its port, approve AI metadata analysis, then select **Prepare & review
Dockerfile**. For GitHub, choose **New deployment → GitHub link**, enter the public
URL, then select **Prepare Dockerfile from GitHub snapshot**. That imports an
immutable copy of the public default branch; confirm the detected port, approve AI
analysis, and prepare/review the Dockerfile on that copy. The repository on GitHub
is never modified. You can deploy the reviewed snapshot as ZIP source, or download
and commit the reviewed Dockerfile before deploying directly from GitHub.

For detected multi-tier applications the generator is instructed to produce one
repository-root Dockerfile, build application tiers from root-relative paths, and
keep stateful services such as databases and caches external. Inspect the generated
Dockerfile and quality report, then review the complete source before deployment.
The web flow runs static Dockerfile/build-input checks and a source dependency Scout
gate; it does **not** build or start the image or scan image CVEs. Use the local
`ebkit doctor --production --verify --scout` workflow above for those checks.

### New: pre-deployment Deployment doctor

`ebkit doctor` adds a local, source-aware check before deployment. It looks for
common Dockerfile and application deployment blockers, shows targeted repair diffs
before applying them, and can optionally build and run the container locally for
an HTTP health probe. Production checks, registry-resolved base-image pins, and an
optional Docker Scout image scan extend that workflow. It can also review a public
GitHub repository by downloading a commit-pinned snapshot into an empty local
directory. Static findings are heuristic; only checks that actually run are reported
as verified.

### How this differs from AWS EB CLI

AWS's `eb` CLI is a general-purpose client for creating, deploying, and managing
Elastic Beanstalk applications and environments. EBKit's `doctor` complements that
kind of deployment workflow rather than replacing it: it reviews your project and
Docker setup before deployment, and its optional build/HTTP checks run locally.
Doctor does not provision or change AWS resources and does not require AWS or AI
credentials. By contrast, `ebkit diagnose` investigates an existing deployment
using AWS build logs and Elastic Beanstalk events; optional Gemini advice is a
separate, explicitly requested step.

EBKit's deployment command is also opinionated around Elastic Beanstalk Cluster
Mode: AWS CodeBuild builds the root Dockerfile, pushes the image to ECR, and EBKit
creates or updates the environment. Use AWS EB CLI when you need its broader
Elastic Beanstalk command workflow; use EBKit when you want this Docker-focused
preflight and its integrated Cluster Mode deployment path.

- **`ebkit init`** — Generate the root Dockerfile and deployment files, validate
  them, and scan source dependencies with Docker Scout. Interactive init asks before
  a local build; `--no-scout` skips security scanning and reports it as unverified.
- **`ebkit deploy GITHUB_URL_OR_ZIP`** — Build the application's root Dockerfile in AWS
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

The source must have a Dockerfile at its root. Uploaded ZIPs may contain one enclosing project folder; EBKit safely flattens it before building.

ZIP preparation and CLI init check missing Dockerfile build inputs and known Vite/Node/React-plugin incompatibilities before deployment. Invalid Gordon drafts fall back to Gemini during website preparation; invalid fallback drafts are blocked. These are static checks, not a successful image-build guarantee. Dependencies are never silently changed, and Scout remains a separate security check.

### AWS IAM permissions and roles

EBKit uses an operator/caller role plus AWS service roles for Cluster Mode and
CodeBuild. Review the role names, managed policies, inline build policy, and caller
permissions in the [AWS IAM guide](docs/aws-iam.md). Use least privilege; do not use
`AdministratorAccess` as a routine deployment role.

### Options
| Option | What it does |
|---|---|
| `SOURCE` | Public GitHub repository URL or local project ZIP |
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

## Initialize and deploy a ZIP

The website detects a suggested container port before preparation; review and correct it. Existing ZIP projects offer **Upload ZIP & redeploy** to update the same environment and URL. CLI users can deploy a new reviewed ZIP with the same `--app` and `--environment`. GitHub pushes automatically redeploy active website projects while the worker is running; use `ebkit watch` for CLI projects. ZIP updates are manual.


```powershell
# Local init uses your configured AI provider and the existing Scout checks.
ebkit init --zip ./project.zip --output ./project-ebkit
# Review ./project-ebkit/Dockerfile, then use the exported archive.
ebkit deploy ./project-prepared.zip --reviewed --app my-app --region us-east-2 --port 8080 --env-file .env
```

For CLI users using the website operator's Gemini key, log in on the website and open **Deploy with CLI ? Copy CLI session token**. Configure:

```powershell
$env:EBKIT_SERVICE_URL = "https://YOUR-EBKIT-WEBSITE"
$env:EBKIT_ACCESS_TOKEN = "YOUR-ACCOUNT-SESSION-TOKEN"
ebkit init --zip ./project.zip
```

Hosted init tries Docker AI (Gordon) first, then Gemini if Gordon is unavailable or fails. It prints the provider and Dockerfile for explicit review before saving the prepared ZIP. The Gemini key stays on the server; users supply their account session, not an AI key. Use `http://127.0.0.1:8787` for this local website backend. Hosted ZIP init runs Docker Scout on source dependencies by default after generation, without an image build. A failed security gate exits with findings and remediation guidance. The explicit `--no-scout` option remains available and prints SECURITY NOT VERIFIED; bypassing does not fix CVEs.

ZIP limits are 25 MB compressed, 200 MB expanded and 5,000 entries. Unsafe paths, links, duplicate names, encryption and excessive compression are rejected. One enclosing folder is flattened. Common `.env`/credential files and installed dependencies are omitted; review source for any other secrets before uploading. The original ZIP is unchanged. `--yes` does not bypass Dockerfile approval: ZIP deploy requires `--reviewed`, or an interactive review confirmation.

CodeBuild builds ZIP source from private encrypted S3 storage. No local Docker build is needed. Source objects are deleted after the build, with a one-day lifecycle rule for abandoned objects; destroy removes the verified application-owned source bucket. ZIP deployment therefore needs scoped S3 permissions in addition to the existing build/deployment permissions. Databases, traffic and source/build resources may incur charges.

## Generate deployment files with `ebkit init`

Run `ebkit init --path .` for a local project or `ebkit init --zip project.zip` for
a ZIP. GitHub links are deployment-only. Init generates or validates deployment files, including
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

- Deployment accepts public GitHub repositories and reviewed project ZIPs with a root-level Dockerfile. Local directories, prebuilt image tags, and Compose deployments are not supported.
- The application image should not bundle a local database for production. Use a managed database service; application-specific migrations and rollback are not automated.
- Gemini is the only active AI provider for `ebkit init`.

### Website trial access

Hosted website deployments offer one operator-approved 30-minute free trial per account, one replica, a persistent countdown and automatic cleanup. Ordinary free accounts can redeploy updated code to the same active trial environment; rollback remains unavailable. Operators may enable a no-payment two-hour test pass for specific accounts with `EBKIT_PAID_TEST_EMAILS`; these accounts must also be in `EBKIT_ALLOWED_EMAILS`. The pass starts at Ready/Green, allows one replica and receives automatic cleanup. The Pricing card continues to display the planned **₹49** price, but allowlisted test-pass use does not charge or open checkout. Provisioning is limited to 30 minutes. Global deployment slots default to two, including timed passes awaiting verified cleanup. See `web/README.md` for migration, scheduler operation and cleanup limitations. Local CLI deployments in your own AWS account are not time-limited by this website trial policy.

Use `ebkit deploy SOURCE --max-replicas 1` to explicitly cap autoscaling; the hosted free trial sets this automatically. Without this option, new CLI environments default to a maximum of five replicas and redeploy preserves existing bounds.


Redeploy changed code with `ebkit deploy https://github.com/owner/repository --yes`.
Within the configured AWS account and region, EBKit reuses a unique saved target,
including custom application/environment names and its port. Each invocation builds
and deploys a fresh image version. Existing environment variables, replica bounds,
and the URL remain in place unless you explicitly change their settings.
If multiple saved environments match, specify `--app` and `--environment`.
For a renamed ZIP, use `ebkit deploy updated.zip --app YOUR_APP --environment YOUR_ENV --reviewed --yes`.
On the website, GitHub default-branch changes redeploy automatically while the worker and deployment are active. You can also use **Redeploy in AWS**, or
**Upload ZIP & redeploy** on the existing project. Updates keep the original trial deadline.


### Watch GitHub from the CLI

After the first deployment, keep `ebkit watch https://github.com/owner/repository --yes`
running to check the default branch every 300 seconds and automatically deploy each
new commit to the existing environment. Use `--app`, `--environment`, and `--region`
when multiple saved deployments match. `--interval 120` changes the polling interval;
`--once` checks once and exits. Ctrl+C stops watching without destroying the deployment.
The watcher preserves the saved port, URL, environment settings and replica bounds,
builds a pinned commit, waits for AWS operations, and never recreates a removed environment.
ZIP updates remain manual. Older deployments without a saved commit establish a baseline
on their first check. Failed commits are not retried automatically; a new push or manual
`ebkit deploy` can retry. Watch progress persists in deployment metadata. One watcher per
target is allowed; after a process crash, the error identifies its stale lock file.
An optional `EBKIT_GITHUB_TOKEN` increases GitHub API limits; keep it in your local environment.


### Environment operations

- `ebkit --profile development health --app APP --environment ENV` shows health causes and application metrics; add `--refresh` for continuous monitoring.
- `ebkit events --app APP --environment ENV --follow` polls for and prints new deployment events until Ctrl+C.
- `ebkit options --app APP --environment ENV` lists full configuration option names with values hidden. Apply a JSON array of `Namespace`, `OptionName`, and `Value` with `--file options.json --yes`, or remove a setting with `--remove NAMESPACE/OPTION`.
- `ebkit config-save --app APP --environment ENV --name staging --yes` and `config-apply` save/apply AWS configuration templates. AWS supports these templates only in Standard Mode; Cluster Mode uses `options`.
- `ebkit clone --app APP --environment ENV --name staging-env --yes` creates a separately tracked environment using the source version and managed settings. It creates billable resources and does not copy external database data. Use `--no-wait` to return after creation starts.
- `ebkit swap --app APP --environment blue-env --destination green-env --yes` swaps two Ready environments' CNAMEs. Verify both with `status` afterward.
- `ebkit version-delete --app APP --environment ENV --version UNUSED_LABEL --yes` removes an unused application version. Versions used in active environments are rejected, and ECR images are retained.
- `ebkit operations cost --app APP --environment ENV` reports environment-tagged Cost Explorer spend, its past-spend monthly run-rate estimate, and AWS's forecast for the rest of the current month. `remainingMonthForecastUsd` is `null` when Cost Explorer cannot provide a forecast; other AWS errors are surfaced. This is not a complete account bill: shared, untagged, and recently incurred charges may be missing.

The web exposes these controls in project **Settings ? Cloud controls**, with health and events also available in **Logs**. Advanced mutations require an operator-designated testing/evaluator account and exact environment-name confirmation. Clone reservations obey the existing quota and trial cleanup policy. Web swaps require both projects to belong to the signed-in user and share an application and region. Settings values are kept out of project metadata and logs.
