# EBKit DevOps implementation and deployment

Author: Rakesh Ku Patra
Task date: 17 October 2026 in Asia Kolkata
Repository: https://github.com/Rakesh-Patra/ebready
Branch: `feat/modern-workspace-deployment-improvements`

This guide explains how an application idea becomes a tested Docker image and a
repeatable AWS deployment. The practical project is EBKit, a React dashboard with
a Node API and Python deployment tools. The target for this exercise is the
dashboard itself on a **standard Elastic Beanstalk Docker environment**. That is
separate from the AWS Cluster Mode environments that EBKit can manage for users.

The aim is to demonstrate decisions, automation, security, validation and recovery.
Using more tools is not the goal. Ansible is unnecessary because Beanstalk manages
the host. A separate Prometheus or Grafana deployment is unnecessary for this demo;
enhanced Beanstalk health and seven days of application/platform logs are enough.

## What each tool does

- **Git and GitHub** preserve source history and isolate work on a feature branch.
- **CI** installs dependencies, runs tests, builds the UI and container, and checks infrastructure syntax.
- **DevSecOps checks** scan application dependencies, source secrets and the container before deployment credentials are requested.
- **Docker** packages the frontend, API and Python tools into one reproducible runtime. The final container runs as a nonroot user on port 8080.
- **Terraform** owns the environment, IAM roles, release bucket and image repository. A reviewed plan is applied before the first delivery.
- **OIDC** exchanges a GitHub identity token for a short lived AWS role session. There is no stored AWS access key in GitHub.
- **ECR** stores the already scanned image. Immutable tags prevent overwrites; the release uses the image digest.
- **S3** holds a small private versioned release ZIP containing `Dockerrun.aws.json`.
- **Elastic Beanstalk** provisions the managed Docker host, pulls the image, and reports deployment health.

## Architecture

```mermaid
flowchart LR
  A[Feature branch and pull request] --> B[CI tests and build]
  B --> C[Source and image security scans]
  C --> D[Verified image artifact and SBOM]
  D --> E[Production deployment job]
  E -->|GitHub OIDC token| F[AWS STS scoped role session]
  F --> G[Private ECR image by digest]
  F --> H[Private S3 release bundle]
  H --> I[Elastic Beanstalk Docker environment]
  G --> I
  I --> J[Health checks logs and smoke tests]
  T[Terraform reviewed plan] --> I
  T --> F
  T --> G
  T --> H
```

## Prerequisites

1. A GitHub repository and permission to configure its Actions variables and deployment environments.
2. An AWS account and a local bootstrap identity permitted to create the named Beanstalk resources, IAM roles, S3 bucket and ECR repository.
3. Terraform 1.14.9, Node 22, Python 3.12 or later, Git and the AWS CLI.
4. An existing GitHub OIDC provider at `https://token.actions.githubusercontent.com` in AWS IAM. This account already has one; Terraform reads and reuses it.
5. A reviewed current Amazon Linux 2023 Docker platform in the target region.
6. A custom domain and ACM certificate for an authenticated public deployment. The default Beanstalk hostname is not automatically HTTPS.

The initial bootstrap uses the operator's AWS profile. Subsequent application
delivery uses OIDC from GitHub. Do not describe the bootstrap as an OIDC login.

## Step 1 Start with a clear application boundary

The app serves the React build, `/api` and the `/insforge` authentication proxy from
one origin. Server-only administration keys must never be included in the React
bundle. The public InsForge URL and anonymous key are public application settings;
they do not grant server administration rights.

For this practice environment, `EBKIT_PUBLIC_DEMO=true` disables the authentication
proxy and removes credential entry from the UI. `EBKIT_LIVE_ENABLED=false` and
`EBKIT_GITHUB_AUTO_DEPLOY=false` prevent this public dashboard from launching
customer AWS jobs. Visitors can still explore the guest simulation. This is a
demonstration deployment, not the full production multi-user control plane.

## Step 2 Use a feature branch

```powershell
git switch -c feat/modern-workspace-deployment-improvements
git fetch origin
git rebase origin/main
git diff --check
```

The branch was created, synced with the newer `main`, and pushed. A conflict in the
infrastructure tools UI was resolved while preserving exact confirmation and
Ready-state checks. A source scan found a deliberately fake AWS key in a redaction
test rather than a real credential. Do not commit `.env`, AWS profiles, job state,
Terraform plans or Terraform state.

## Step 3 Test and build locally

From the repository root:

```powershell
python -m pytest tests/test_aws_tls.py tests/test_managed_database.py tests/test_cluster_health_poll.py tests/test_controls.py tests/test_production_ops.py -q
cd web
$env:EBKIT_TEST_PYTHON=(Get-Command python.exe).Source
npm.cmd ci
npm.cmd test -- --pool=threads --maxWorkers=1 --testTimeout=15000
npm.cmd run build
cd ..
```

Earlier verified baseline: 52 relevant Python tests, 223 web tests and the production
build passed. New delivery and public-demo changes require their own fresh CI run;
use the current Actions run as the release evidence rather than reusing baseline counts.

Windows test troubleshooting: the default test process pool timed out on this
machine. A single threads worker completed. The ZIP tests also need a real Python
executable through `EBKIT_TEST_PYTHON`. These are runner configuration details,
not evidence that unexecuted tests passed.

## Step 4 Package the application with Docker

Read the root `Dockerfile` and `.dockerignore`.

The build stage runs `npm ci` and the Vite production build. The runtime stage
installs production Node dependencies and the Python CLI. It copies the built
frontend and server, runs as the `node` user, exposes port 8080, and includes a
health check against `/api/health`.

```bash
docker build -t ebkit:ci .
docker run --rm -p 8080:8080 -e EBKIT_PUBLIC_DEMO=true ebkit:ci
curl --fail http://127.0.0.1:8080/api/health
```

The local Docker daemon was unavailable during initial preparation. The GitHub
runner builds and scans the actual delivery image; do not claim a local image test
unless you have recorded it separately.

## Step 5 Define infrastructure in Terraform

Configuration lives under `infra/terraform`. The stack creates a single-instance
practice environment, private encrypted release bucket, immutable ECR repository,
instance profile, Beanstalk health role and GitHub deployment role. The existing
GitHub OIDC identity provider is read rather than recreated.

```powershell
$env:AWS_PROFILE='default'
terraform '-chdir=infra/terraform' init
terraform '-chdir=infra/terraform' fmt -check
terraform '-chdir=infra/terraform' validate
terraform '-chdir=infra/terraform' plan '-var-file=deployment.tfvars.json' '-out=deployment.tfplan'
terraform '-chdir=infra/terraform' apply deployment.tfplan
```

The initial reviewed plan had 16 creations and no changes or deletions to existing
resources. Keep the lock file under version control. The local state and input
files are ignored; move the state to an access-restricted, encrypted remote backend
with locking before team use. Do not pass secret values into Terraform variables.

A real troubleshooting event occurred during bootstrap: the newest AWS provider
used an S3 Control endpoint that failed DNS resolution. Some resources had already
been created. The recovery is to inspect saved state, pin the compatible provider,
review the refreshed plan and resume. Never blindly recreate resources after a
partial apply.

## Step 6 Set up GitHub OIDC securely

The trust policy requires both:

```text
aud = sts.amazonaws.com
sub = repo:Rakesh-Patra/ebready:environment:production
```

The production GitHub environment restricts deployment branches to the feature
branch used for today's demonstration and `main`. The workflow additionally
requires `github.ref_name == vars.DEPLOYMENT_BRANCH`. Fork pull requests run CI
without a cloud identity and cannot execute the deployment job.

Only the deploy job receives `id-token: write`. It exchanges the token using
`aws-actions/configure-aws-credentials`. Its role can push to this ECR repository,
write the release prefix in this S3 bucket, create versions for this application,
and update this one environment. It cannot create IAM roles or run Terraform apply.

Repository Actions variables contain only configuration:

```text
AWS_REGION
AWS_ROLE_ARN
EB_ARTIFACT_BUCKET
ECR_REPOSITORY
EB_APPLICATION
EB_ENVIRONMENT
DEPLOYMENT_BRANCH
```

`scripts/github_delivery.py configure` sets these variables and the branch policy
using an existing Git Credential Manager credential in memory. It never writes or
prints that credential. Do not paste a GitHub token into the script or this guide.

## Step 7 Keep secrets in the right place

- AWS delivery credentials: generated temporarily through OIDC, not GitHub secrets.
- Public app settings: normal environment variables; no secrets are required for guest demo mode.
- Future server secrets: create the value outside Terraform in AWS Secrets Manager; pass only its ARN through `secret_arns`.
- Instance permission: `secretsmanager:GetSecretValue` is limited to those exact ARNs. Customer-managed KMS keys also require a reviewed `kms:Decrypt` grant.
- React settings: anything named `VITE_*` is browser-visible. Never place passwords, admin keys or cloud credentials there.
- Artifacts: the release ZIP contains only `Dockerrun.aws.json`. The scanned Docker image contains application source and runtime dependencies, not local environment files.

Beanstalk's `aws:elasticbeanstalk:application:environmentsecrets` namespace resolves
the secret during instance bootstrap on supported platform versions. Changing a
secret does not automatically prove that a running app has refreshed it; perform
and verify the appropriate environment refresh/redeployment.

## Step 8 Run CI and security gates

The pinned workflow is `.github/workflows/delivery.yml`.

The verification job runs relevant Python tests, all web tests, frontend build,
production dependency audit, Terraform format/validation, Docker build, Trivy
source and image scans, and CycloneDX SBOM generation. Scanner gates cover High
and Critical findings with available fixes; ignored unfixed findings are not a
claim of zero vulnerabilities. Reports are saved for review.

The scanned image is saved as a short-retention Actions artifact. Deployment
loads that exact image rather than rebuilding it. Action dependencies use commit
hashes, and Dependabot proposes updates. Scan findings should be fixed or reviewed
explicitly; do not disable the gate to obtain a green screenshot.

## Step 9 Deploy with short lived credentials

After CI succeeds, the production job obtains an AWS role session, logs in to ECR,
pushes a unique immutable image tag and reads its digest. The release script
validates that digest and writes a versioned Dockerrun bundle. It uploads that
bundle over TLS to private S3 and creates an Elastic Beanstalk application version.

`scripts/release_dashboard.py` waits for bundle processing, updates the named
environment and waits for Ready/Green on the requested version. It records the
previous version before making the update. Terraform intentionally ignores
`version_label` drift so infrastructure ownership and application delivery do
not fight each other.

## Step 10 Verify the actual release

Acceptance evidence must show all of the following:

1. The Actions verification job succeeded and the deployment job actually ran.
2. OIDC role assumption succeeded without stored AWS access keys.
3. The image digest in the receipt matches the ECR image used by Dockerrun.
4. Beanstalk reports Ready and Green on the expected version.
5. The homepage, built JavaScript asset and `/api/health` return HTTP 200.
6. Health reports that hosted live cloud controls are disabled.
7. Public demo authentication requests are rejected; no passwords are submitted over HTTP.

The release receipt records the commit, run URL, image digest, application version,
previous version, endpoint, health and smoke results. A successful Terraform apply
alone is not proof that the EBKit application was deployed: the initial Beanstalk
sample application may be running before the first release job.

## Step 11 Demonstrate recovery

For a failed release, inspect Beanstalk Events, enhanced health and CloudWatch logs.
Use the receipt's previous application version after review:

```bash
aws elasticbeanstalk update-environment --region us-east-2 \
  --environment-name ebkit-dashboard-practice --version-label PREVIOUS_VERSION
```

Then repeat the same health and asset smoke checks. Do not claim a rollback test
until it was executed and verified. Application rollback does not roll back an
external database or destructive schema migration.

## Screenshots and evidence to submit

Capture actual tool or browser state, with the run time visible where possible:

1. GitHub branch and commit history showing today's source changes.
2. A successful Actions run with separate verification and deployment jobs.
3. The deploy step showing successful OIDC role assumption, with sensitive identifiers masked where useful.
4. Terraform's reviewed plan and successful apply summary.
5. ECR repository showing the immutable image digest.
6. Beanstalk overview showing the requested version and Ready/Green state.
7. The actual application in a browser and its public-demo notice.
8. The health endpoint and stored smoke-test receipt.
9. A security report and SBOM artifact.

Do not use generated mockups as deployment screenshots. A prior UI design image
can be included only when captioned as a design preview. If browser capture is
unavailable, provide raw receipts and state which screenshots still need capture.
Never screenshot passwords, access tokens, `.env` contents or secret values.

## Explain the project to judges

Start with the application problem and show the UI briefly. Then follow one source
commit through tests, security checks, the scanned image digest, OIDC login and the
running Beanstalk version. Explain why the delivery role cannot administer IAM.
Show the deployment receipt and a health check. Finish by explaining the previous
version rollback and the cleanup process. Present unfinished HTTPS or protection
work as a limitation rather than claiming production readiness.

Suggested demonstration order: application idea, source branch, CI results,
security report, Terraform plan, OIDC trust, ECR digest, live release, health,
recovery and cost controls. This gives a coherent story rather than a list of tools.

## Cleanup and production differences

Keep the environment running only while needed for judging. Review Terraform's
destroy plan before terminating this named stack. The release bucket is versioned
and `force_destroy=false`; retained objects must be reviewed before deletion.
ECR images can also block repository deletion. Existing OIDC providers are shared
and are not owned or deleted by this stack.

Before production: configure domain and ACM-backed HTTPS, deliberately re-enable
authentication, review allowed origins, use a scoped server execution model for
customer AWS work, add an encrypted remote Terraform backend, define service
objectives, and choose resilience appropriate to the application. A single
instance practice environment is not highly available.

## References

- [AWS Docker configuration](https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/create_deploy_docker.container.console.html)
- [AWS enhanced health API configuration](https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/health-enhanced-api.html)
- [AWS secret references in environment variables](https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/AWSHowTo.secrets.env-vars.html)
- [GitHub OIDC with AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)
- [GitHub deployment environments](https://docs.github.com/en/actions/concepts/workflows-and-actions/deployment-environments)
- [Terraform Elastic Beanstalk environment resource](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/elastic_beanstalk_environment)

## Execution evidence

The final results for today's deployment are appended here after verification.
Until that happens, infrastructure and workflow files are implementation evidence,
not proof of a successful public application release.
