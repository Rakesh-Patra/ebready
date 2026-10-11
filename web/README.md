# EBKit web application

The React dashboard uses **InsForge for accounts and project storage**, with Google OpenID Connect and email/password authentication. A Node backend validates public GitHub repositories and runs the Python EBKit CLI for live AWS deployments and management.

## Run locally

Use Node.js 22.12+ and Python 3.11+. From the repository root, install EBKit with `py -m pip install .`, then run `npm.cmd ci` inside `web`.

Start two terminals in `web`:

```powershell
# Terminal 1
npm.cmd run backend

# Terminal 2
npm.cmd run dev
```

Open **http://127.0.0.1:5173/**. Both processes are already running in this workspace. Vite proxies `/api` to the local backend on port 8787. Startup makes no AWS calls.

## Accounts

**Create account** registers an email/password account. Enter the emailed verification code to open the account. **Log in** accepts an existing account. **Continue with Google** uses OpenID Connect through InsForge's PKCE flow. **Forgot password** requests an email code, exchanges it for a reset token, then saves the new password.

The current branch uses code-based verification and password reset; keep those methods for these forms. Google redirects to the exact frontend origin. `web/insforge.toml` records the local callbacks. Add the exact HTTPS origin before public hosting.

## Configuration

The gitignored `web/.env` contains only public `VITE_INSFORGE_URL` and `VITE_INSFORGE_ANON_KEY`. Never put an admin key, AWS key or application secret in a `VITE_` variable; it becomes part of the browser bundle.

The gitignored `web/server/.env` is configured for the linked `ebkit-web` branch. On another machine copy `server/.env.example` to `server/.env` and set:

```dotenv
INSFORGE_URL=
INSFORGE_ANON_KEY=
INSFORGE_API_KEY=
EBKIT_PYTHON=
EBKIT_LIVE_ENABLED=false
EBKIT_ALLOWED_EMAILS=
EBKIT_WEB_ORIGINS=http://127.0.0.1:5173,http://localhost:5173
```

`INSFORGE_API_KEY` is the server-only project admin key. `EBKIT_PYTHON` is the full path to Python with EBKit installed. Set `EBKIT_LIVE_ENABLED=true` and list approved account emails in `EBKIT_ALLOWED_EMAILS`, separated by commas. This workspace allows the current InsForge operator's email. Restart the backend after changing its configuration.

The worker uses its standard AWS credential chain: configured AWS profile, process environment, or IAM role when hosted. **Users never provide AWS credentials through the website.** The worker's AWS identity pays for live resources. Use a dedicated, scoped role. Users deploying into their own AWS account should use EBKit from their terminal.

The worker binds only to loopback. Do not expose the development Vite server publicly.

## Deploy and manage

1. Log in using an operator-approved account.
2. Click **New deployment**, enter a public GitHub URL and acknowledge the root Dockerfile requirement.
3. **Check repository & continue** checks GitHub metadata and the root `Dockerfile` on the default branch. Missing/private repositories, missing Dockerfiles, rate limits and connectivity errors appear inline. This verifies presence, not build correctness or CVEs.
4. Enter a project name, region, port and optional `KEY=VALUE` settings. Importing `.env` reads the file locally. In live mode, values are sent only on deployment, passed through a temporary server file to EBKit, then removed after the operation. The project database stores variable names only. Known supplied values are redacted from worker output. Applications should never log secrets themselves.
5. Confirm AWS charges and click **Deploy to AWS**. EBKit uses CodeBuild, ECR and Elastic Beanstalk Cluster Mode. Logs contain actual CLI output. Ready requires the saved AWS deployment result to report Ready and Green; failures remain failures.

Project controls:

- **Redeploy in AWS** starts a real build/deployment. Existing environment settings are preserved when no replacement values are supplied.
- **Status / Logs / Versions / Resources / Cleanup-status** run the corresponding EBKit command and show its output in Logs.
- **Apply AWS scaling** changes replica bounds from 1 to 100 and affects compute charges. Cluster Mode cannot scale to zero.
- **Rollback in AWS** requires an existing version label. Load Versions in Logs, enter its exact label in Settings, then invoke rollback from Overview.
- **Destroy** requires the generated environment name. It terminates the environment and cleans eligible EBKit artifacts while retaining cleanup metadata. Shared infrastructure and external services remain. Managed EKS deletion can stay pending and continue charging after environment termination; inspect Cleanup-status.

The pilot worker runs one operation at a time. Each user can have three active live projects, with five launches per worker process. Keep the operator allowlist; durable quotas and billing controls are needed before open public paid deployments.

For a missing Dockerfile, run `ebkit init` locally, review the generated Dockerfile and Scout findings, then push it. The existing CLI Scout bypass and warning remain available. The web presence check does not run Scout.

## Demo mode

**Switch to demo** enables controls without AWS actions. GitHub validation still runs. Build/security/health stages are explicitly simulated. Guest demo projects disappear on refresh; signed-in projects persist and simulated progress resumes after reload. Demo `.env` import keeps names and discards values.

## Database security

Website changes are isolated in the `ebkit-web` schema-only branch. Apply both root migrations when setting up another intended project:

- `20261011060931_ebkit-web-projects.sql`
- `20261011062758_ebkit-web-live-worker.sql`

Owners can read their projects through row-level security. Browser clients may create/update/delete only their own demo records. The authenticated worker writes live AWS records and results after checking ownership and the operator allowlist. Clients cannot change ownership or live status.

## Verify

```powershell
npm.cmd test -- --pool=threads --maxWorkers=1
npm.cmd run build
npm.cmd audit
```

Tests cover account flows, wizard clicks, GitHub checks, command construction, ownership, allowlist enforcement, secret redaction and worker execution using mocked AWS subprocesses. These checks do not create billable AWS resources. User sign-in and AWS role permissions still require end-to-end verification; unit tests do not prove them.

## Public hosting

Serve `dist/` and proxy `/api` to the worker over HTTPS. Configure that origin in InsForge and `EBKIT_WEB_ORIGINS`, provide server-only secrets, and use a dedicated IAM role. Run the worker under a process supervisor with a private persistent `.jobs` volume for deployment state. A restart does not automatically recover an interrupted operation; inspect AWS Status/Resources before retrying.

Before broader production use, replace the single-process worker with a durable queue, shared quotas, encrypted secret storage, rate limiting and automatic recovery.

### Required environment settings

Repository validation reads public example environment templates and up to 40 small source files (64 KB each) without executing code. It detects common runtime environment accesses in several languages, omits EBKit-managed PORT/HOST settings, and returns names only. Required names are inserted with blank values; enter connection values or import a local `.env`. The deploy button stays hidden while required values are missing, and the backend independently rejects the initial deployment before creating project/AWS resources.

Detection is heuristic. Blank template settings and common database/cache connection references are treated as required; optional template comments and defaults are respected. Where runtime references are available, unused template entries (such as Compose-only settings) are omitted. Review repository documentation for dynamic settings or dependencies the scan cannot identify. This check does not provision or test the database connection. Existing deployments retain settings during manual redeploy.
