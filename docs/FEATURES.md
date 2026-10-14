# EBKit in simple terms

EBKit prepares an application for containers, deploys it to AWS, and helps manage it afterward. The CLI runs from your terminal. The website provides forms and buttons backed by the same CLI.

## Prepare the application

- **Init:** analyze a local project or ZIP and generate a root Dockerfile and deployment files. Review generated files before deployment.
- **Doctor:** find deployment problems, show proposed repairs, and optionally build, run and scan the image.
- **Website:** upload a ZIP or import a public GitHub snapshot, detect settings, prepare the Dockerfile, review it and download the prepared project.

## What init checks

`ebkit init --path ./your-project` generates deployment files. Scout checks source dependencies by default. A source check does not scan a built container image.

`ebkit init --path ./your-project --build --runtime --scout` additionally builds the image, starts the app and checks its HTTP response, and scans the image for CVEs. Apps needing a database also need a reachable database and correct runtime settings. A generated Dockerfile alone does not establish build success, runtime health or absence of vulnerabilities.

Use `ebkit doctor --path ./your-project --production --require-distroless --verify --scout` to enforce production policy and verify an existing Dockerfile.

## Deploy and update

- **Deploy:** build GitHub code or a reviewed ZIP in AWS CodeBuild, store the image in ECR and deploy it to Elastic Beanstalk.
- **Watch:** the CLI checks GitHub every 300 seconds and deploys new commits while it is running. The website worker provides the corresponding automatic update feature.
- **Redeploy:** update the same application/environment. Existing URL and settings are preserved when no replacement is requested. ZIP updates are manual.
- **Versions and rollback:** inspect releases and restore an earlier application version. Database schema changes are not automatically reversed.

## Inspect and manage

Status, logs, events, health, resources and diagnose explain the current deployment and failures. Config updates environment variables; options updates AWS environment settings. Scale changes replica bounds. Clone creates another environment; swap exchanges two environment URLs. Version-delete removes an unused release. Destroy removes eligible deployment resources; cleanup-status checks whether deletion actually completed.

Config-save and config-apply use AWS saved configuration templates for Standard environments only. Cluster environments use options instead.

## What "production operations" means

These are extra tools for running an app after deployment, under `ebkit operations ACTION` in the CLI and Production operations in the website:

- **Database:** create private PostgreSQL, configure network access, check it, attach its connection, run reviewed migrations, snapshot and delete it.
- **Monitoring:** fetch CPU, memory, replicas and available load-balancer traffic history; create CloudWatch alarms.
- **Autoscaling:** configure CPU/memory triggers and minimum/maximum replicas.
- **CI/CD:** generate a GitHub Actions workflow with app tests, AWS OIDC and an approval environment. The owner must configure the role, reviewers and commit the files.
- **Release checks:** run configured tests before deployment, verify health afterward and optionally restore the previous version after failure.
- **Costs:** inspect tagged costs/forecasts, create a budget alert and list retained resources. These reports do not stop spending or cover every shared resource.

The website also provides accounts, ownership checks, deployment logs, demo mode and approved timed test passes. Live website actions use the operator's AWS account. CLI actions use your local AWS credentials. Advanced website actions require operator-designated access; timed passes restrict some actions.

See [all 24 CLI commands](COMMANDS.md), [operation settings](OPERATIONS.md), and the [14 October live verification report](live-cli-verification-2026-10-14.md). An automated mock test is not a live integration test.
