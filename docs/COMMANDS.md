# CLI command reference

EBKit registers 24 top-level commands. `--version` and `--help` are global options, not commands.

- **`cleanup-status`** - Check recorded managed cluster deletion after destroy (read-only).
- **`clone`** - Create another environment with the current version and managed settings.
- **`config`** - List variable names or update settings without rebuilding (values are never printed).
- **`config-apply`** - Apply an AWS configuration template (Standard Mode only).
- **`config-save`** - Save an AWS configuration template (Standard Mode only).
- **`deploy`** - Deploy a GitHub URL or a reviewed local project ZIP using AWS CodeBuild.
- **`destroy`** - Terminate an environment and clean up eligible EBKit artifacts after confirmation.
- **`diagnose`** - Analyze the latest deployment failures and suggest bounded, safe next steps.
- **`doctor`** - Find deployment blockers, review repairs, and verify locally.
- **`envlist`** - List Elastic Beanstalk environments in the configured AWS account and region.
- **`events`** - Read deployment events, optionally streaming new events.
- **`health`** - Show environment health causes, application metrics and latency.
- **`init`** - Initialize EBReady deployment kit with interactive setup wizard.
- **`logs`** - Retrieve CodeBuild and Elastic Beanstalk deployment logs.
- **`operations`** - Operate databases, metrics, alarms, autoscaling and budgets.
- **`options`** - List option names or apply full environment options; values stay hidden.
- **`resources`** - Show live environment resources and recorded image/build identifiers (read-only).
- **`rollback`** - Deploy an existing version; database changes and settings are not rolled back.
- **`scale`** - Change Cluster replica bounds (at least one replica; this does not stop EKS charges).
- **`status`** - Show environment health, deployment status, URL, and deployed version.
- **`swap`** - Swap two environment URLs for a blue/green deployment.
- **`version-delete`** - Delete an unused application version; retained ECR images are not deleted.
- **`versions`** - List application versions available for rollback.
- **`watch`** - Automatically redeploy new GitHub commits to an existing environment.

Run `ebkit COMMAND --help` for options. See [CLI guide](CLI.md) and [production operations](OPERATIONS.md) for setup and examples.

## Verification after restoring the clone

- Python: 477 tests passed. AWS services are mocked; this does not validate live infrastructure.
- Web: 194 tests passed with EBKIT_TEST_PYTHON set to the installed interpreter, two workers and a 20-second timeout. The default five-second run had three UI timeouts. TypeScript/Vite build passed.
- Help: all 24 command help pages exited successfully.
- Dockerfiles: six generated samples passed static production policy checks. Image builds, HTTP runtime checks and CVE scans remain unverified in this recovery session.

## Hands-on checklist for 14 October

Exercise CLI and web deployment from GitHub and ZIP; change GitHub code and verify the 300-second watcher preserves the environment, URL and variables. Check manual ZIP redeployment. Build/run/scan generated images. Check logs, health, events, scaling, rollback, database connectivity/migrations, pipeline tests/OIDC/approvals, monitoring alerts and budget/cost reports. Finish by checking cleanup and retained database resources.

Record the actual outcomes and resource identifiers. Do not describe an unrun live check as passed.
